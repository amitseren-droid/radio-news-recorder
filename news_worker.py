#!/usr/bin/env python3
"""Best-effort free Actions recorder. This is not an always-on recorder."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

STREAM = "https://glzwizzlv.bynetcdn.com/glglz_mp3"
POST_HOUR_SECONDS = 360
PRE_ROLL = 15
MAX_START_DELAY = 5


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def next_hour(now):
    return (int(now) // 3600 + 1) * 3600


def report(message):
    print(message, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def command(args, cwd=None, timeout=60):
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-1800:]}")
    return result.stdout


def remote_state(repo, ref):
    # Missing metadata is expected during migration from V2.5. No commit-age guesses.
    exists = subprocess.run(["git", "cat-file", "-e", f"{ref}:recordings/latest.json"],
                            cwd=repo, capture_output=True, timeout=20)
    if exists.returncode:
        return {}
    state = json.loads(command(["git", "show", f"{ref}:recordings/latest.json"], cwd=repo))
    return state


def already_published(state, target):
    return int(state.get("target_epoch", 0)) >= target


def duration(path):
    info = json.loads(command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path)]))
    seconds = float(info["format"]["duration"])
    if not math.isfinite(seconds) or seconds <= 0:
        raise RuntimeError("Invalid audio duration")
    return seconds


def validate_audio(path, minimum, maximum):
    seconds = duration(path)
    if not minimum <= seconds <= maximum:
        raise RuntimeError(f"Unexpected audio duration: {seconds:.2f}s (expected {minimum}-{maximum})")
    command(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
             "-f", "null", "-"], timeout=90)
    return seconds


def wait_for_capture(target):
    # Never reinterpret a missed target as a recording of that bulletin.
    if time.time() > target + MAX_START_DELAY:
        raise RuntimeError(f"MISSED: preparation completed after target {stamp(target)}")
    start = target - PRE_ROLL
    while time.time() < start:
        time.sleep(min(5, max(0, start - time.time())))
    now = time.time()
    if now > target + MAX_START_DELAY:
        raise RuntimeError("MISSED: runner resumed too late; keeping the previous recording")
    return now, math.ceil(target + POST_HOUR_SECONDS - now)


def publish(repo, audio, metadata):
    """Non-force pushes with fresh remote state on every retry. Audio stays in temp."""
    command(["git", "config", "user.name", "github-actions[bot]"], cwd=repo)
    command(["git", "config", "user.email",
             "41898282+github-actions[bot]@users.noreply.github.com"], cwd=repo)
    for attempt in range(1, 6):
        try:
            command(["git", "fetch", "--quiet", "--depth=1", "origin", "main"], cwd=repo)
            base = command(["git", "rev-parse", "FETCH_HEAD"], cwd=repo).strip()
            if already_published(remote_state(repo, base), metadata["target_epoch"]):
                report("DUPLICATE: this bulletin or a newer one was already published; no overwrite.")
                return False
            # Only reset the disposable runner checkout. Never force-push the remote.
            command(["git", "reset", "--hard", base], cwd=repo)
            folder = repo / "recordings"
            folder.mkdir(exist_ok=True)
            shutil.copyfile(audio, folder / "latest.mp3")
            metadata["published_at"] = stamp(time.time())
            (folder / "latest.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            command(["git", "add", "recordings/latest.mp3", "recordings/latest.json"], cwd=repo)
            command(["git", "commit", "-m", f"Publish Galgalatz bulletin {metadata['bulletin_hour']}"], cwd=repo)
            command(["git", "push", "origin", "HEAD:main"], cwd=repo, timeout=90)
            report(f"PUBLISHED: {metadata['bulletin_hour']}; audio and metadata committed together.")
            return True
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            report(f"Publish attempt {attempt}/5 failed: {exc}")
            if attempt == 5:
                raise
            time.sleep(attempt * 2)
    return False


def record(repo, work, target):
    work.mkdir(parents=True, exist_ok=True)
    # Avoid expensive preparation/recording when the same target already exists.
    try:
        command(["git", "fetch", "--quiet", "--depth=1", "origin", "main"], cwd=repo)
        if already_published(remote_state(repo, "FETCH_HEAD"), target):
            report("DUPLICATE: bulletin already available; no new recording needed.")
            return
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        report(f"Preflight fetch unavailable; still attempt capture: {exc}")

    report(f"TARGET: {stamp(target)}; capture begins 15 seconds early when ready in time.")
    started, seconds = wait_for_capture(target)
    original, final = work / "original.mp3", work / "latest.mp3"
    report(f"CAPTURING: {stamp(started)}, {seconds} seconds.")
    # Bound both network stalls and total execution; never record seven minutes late.
    command(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
             "-rw_timeout", "15000000", "-i", STREAM, "-t", str(seconds),
             "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(original)],
            timeout=seconds + 45)
    raw_seconds = validate_audio(original, seconds - 2, seconds + 2)

    intro_detected = False
    try:
        output = command([sys.executable, str(repo / "detect_intro.py"), str(original),
                          str(repo / "news-intro-reference.mp3"), str(final)], timeout=150)
        print(output, flush=True)
        intro_detected = "CUT: removed" in output or "NO CUT: Intro already near start" in output
        final_seconds = validate_audio(final, 30, raw_seconds + 1)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        report(f"UNCUT FALLBACK: intro processing failed: {exc}")
        shutil.copyfile(original, final)
        intro_detected = False
        final_seconds = raw_seconds

    metadata = {
        "schema_version": 1,
        "recorder_version": "2.6",
        "target_epoch": target,
        "bulletin_hour": stamp(target),
        "capture_started_at": stamp(started),
        "duration_seconds": round(final_seconds, 3),
        "raw_duration_seconds": round(raw_seconds, 3),
        "intro_detected": intro_detected,
        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local-test"),
    }
    report("AUDIO VALIDATED: " + ("intro matched." if intro_detected else "intro not confirmed; original retained."))
    publish(repo, final, metadata)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "record"])
    parser.add_argument("--target", type=int)
    args = parser.parse_args()
    if args.mode == "plan":
        target = next_hour(time.time())
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"target={target}\n")
        report(f"Planned target before installation: {stamp(target)}")
        return
    if args.target is None:
        parser.error("--target is required for recording")
    try:
        record(Path.cwd(), Path(os.environ["RUNNER_TEMP"]) / "news-candidate", args.target)
    except Exception as exc:
        report(f"FAILED: {exc}. Previous published bulletin has not been replaced by this run.")
        print(f"::error::{exc}", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
