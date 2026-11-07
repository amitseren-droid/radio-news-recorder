import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import news_worker as worker


class TimingTests(unittest.TestCase):
    def test_every_minute_is_planned(self):
        for minute in range(60):
            self.assertEqual(worker.next_hour(16 * 3600 + minute * 60 + 20), 17 * 3600)

    def test_day_boundary(self):
        self.assertEqual(worker.next_hour(23 * 3600 + 3599), 24 * 3600)

    def test_no_seven_minutes_late_capture(self):
        with patch.object(worker.time, 'time', return_value=3600 + 7 * 60):
            with self.assertRaisesRegex(RuntimeError, 'MISSED'):
                worker.wait_for_capture(3600)

    def test_waits_until_preroll(self):
        with patch.object(worker.time, 'time', side_effect=[3500, 3585, 3585]):
            self.assertEqual(worker.wait_for_capture(3600), (3585, 375))

    def test_wakeup_late_is_error(self):
        with patch.object(worker.time, 'time', side_effect=[3500, 3620, 3620]):
            with self.assertRaisesRegex(RuntimeError, 'MISSED'):
                worker.wait_for_capture(3600)

    def test_duplicate_uses_bulletin_not_commit_age(self):
        self.assertFalse(worker.already_published({}, 3600))
        self.assertFalse(worker.already_published({'target_epoch': 0}, 3600))
        self.assertTrue(worker.already_published({'target_epoch': 3600}, 3600))
        self.assertTrue(worker.already_published({'target_epoch': 7200}, 3600))

    def test_truncated_audio_rejected(self):
        with patch.object(worker, 'duration', return_value=61.0):
            with self.assertRaisesRegex(RuntimeError, 'duration'):
                worker.validate_audio(Path('fake.mp3'), 373, 377)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'runner'
        self.seed = self.root / 'seed'
        self.git('init', '--bare', '--initial-branch=main', str(self.remote))
        self.git('clone', str(self.remote), str(self.seed))
        self.configure(self.seed)
        (self.seed / 'README.md').write_text('original')
        self.git('add', '.', cwd=self.seed)
        self.git('commit', '-m', 'initial', cwd=self.seed)
        self.git('push', 'origin', 'main', cwd=self.seed)
        self.git('clone', str(self.remote), str(self.repo))
        self.audio = self.root / 'candidate.mp3'
        self.audio.write_bytes(b'fake-audio-already-validated')

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def configure(self, path):
        self.git('config', 'user.name', 'test', cwd=path)
        self.git('config', 'user.email', 'test@example.invalid', cwd=path)

    def metadata(self, target):
        return {'target_epoch': target, 'bulletin_hour': worker.stamp(target)}

    def test_atomic_publish_and_duplicate(self):
        self.assertTrue(worker.publish(self.repo, self.audio, self.metadata(3600)))
        head = self.git('rev-parse', 'main', cwd=self.remote)
        state = json.loads(self.git('show', 'main:recordings/latest.json', cwd=self.remote))
        self.assertEqual(state['target_epoch'], 3600)
        self.assertEqual(self.git('show', 'main:recordings/latest.mp3', cwd=self.remote), self.audio.read_text())
        self.assertFalse(worker.publish(self.repo, self.audio, self.metadata(3600)))
        self.assertFalse(worker.publish(self.repo, self.audio, self.metadata(0)))
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote), head)

    def test_conflicting_push_retries_preserving_user_edit(self):
        actual = worker.command
        failures = []
        def race(args, cwd=None, timeout=60):
            if args[:2] == ['git', 'push'] and not failures:
                failures.append(True)
                (self.seed / 'README.md').write_text('user edit during recording')
                self.git('add', '.', cwd=self.seed)
                self.git('commit', '-m', 'user edit', cwd=self.seed)
                self.git('push', 'origin', 'main', cwd=self.seed)
            return actual(args, cwd=cwd, timeout=timeout)
        with patch.object(worker, 'command', side_effect=race), patch.object(worker.time, 'sleep'):
            self.assertTrue(worker.publish(self.repo, self.audio, self.metadata(7200)))
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.git('show', 'main:README.md', cwd=self.remote), 'user edit during recording')

    def test_failed_publication_does_not_change_remote_audio(self):
        worker.publish(self.repo, self.audio, self.metadata(3600))
        head = self.git('rev-parse', 'main', cwd=self.remote)
        actual = worker.command
        def offline(args, cwd=None, timeout=60):
            if args[:2] == ['git', 'push']:
                raise RuntimeError('simulated upload outage')
            return actual(args, cwd=cwd, timeout=timeout)
        with patch.object(worker, 'command', side_effect=offline), patch.object(worker.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'outage'):
                worker.publish(self.repo, self.audio, self.metadata(7200))
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote), head)
        self.assertTrue(self.audio.exists())


if __name__ == '__main__':
    unittest.main()
