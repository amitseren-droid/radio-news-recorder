#!/usr/bin/env python3
"""Safely trim audio before a known news jingle, or keep it unchanged."""
import argparse
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

RATE = 8000
MIN_SCORE = 0.82
PREROLL_SECONDS = 0.3  # Conservatively preserve the complete musical intro.


def pcm(path, output):
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(path), '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le', str(output)
    ], check=True)
    with wave.open(str(output), 'rb') as wav:
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    return data.astype(np.float32) / 32768.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('reference')
    parser.add_argument('output')
    args = parser.parse_args()
    source = Path(args.input)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def fallback(reason):
        print(f'NO CUT: {reason}. Publishing original recording.', flush=True)
        shutil.copyfile(source, destination)

    try:
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            audio = pcm(source, folder / 'audio.wav')
            ref = pcm(args.reference, folder / 'ref.wav')
            n = len(ref)
            if n < RATE * 3 or len(audio) < n:
                fallback('Reference too short or recording shorter than reference')
                return
            ref = ref - ref.mean()
            energy = np.dot(ref, ref)
            if energy < 1e-4:
                fallback('Silent reference')
                return
            # Normalized cross correlation, tolerant of gain differences.
            numerator = fftconvolve(audio, ref[::-1], mode='valid')
            cs = np.r_[0., np.cumsum(audio, dtype=np.float64)]
            cs2 = np.r_[0., np.cumsum(audio.astype(np.float64)**2)]
            sums = cs[n:] - cs[:-n]
            powers = cs2[n:] - cs2[:-n] - sums*sums/n
            scores = numerator / np.sqrt(np.maximum(powers * energy, 1e-12))
            i = int(np.argmax(scores))
            score = float(scores[i])
            found_at = i / RATE
            print(f'Best intro match: {found_at:.2f}s, confidence={score:.3f}', flush=True)
            if score < MIN_SCORE:
                fallback('Low confidence')
                return
            cut_at = max(0.0, found_at - PREROLL_SECONDS)
            if cut_at < 0.5:
                fallback('Intro already near start')
                return
            subprocess.run([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                '-ss', f'{cut_at:.3f}', '-i', str(source),
                '-c:a', 'libmp3lame', '-b:a', '128k', str(destination)
            ], check=True)
            print(f'CUT: removed {cut_at:.2f}s; retained intro with {PREROLL_SECONDS:.1f}s preroll.', flush=True)
    except Exception as exc:
        fallback(f'Analysis failed: {exc}')


if __name__ == '__main__':
    main()
