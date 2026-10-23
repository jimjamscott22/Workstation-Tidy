import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import Profile
from workstation_tidy.handlers import Handler
from workstation_tidy.models import Target, TidyError
from workstation_tidy.safety import ProcessView
from workstation_tidy.scanner import inspect
from workstation_tidy.service import clean, clean_one
from workstation_tidy.storage import Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / ".cache/thumbnails"
        (self.path / "normal").mkdir(parents=True)
        self.file = self.path / "normal/a.png"
        self.file.write_bytes(b"thumbnail" * 1024)
        self.target = Target("thumb", "thumbnails", self.path, "desktop", "regenerate")
        self.profile = Profile("fixture", self.home, (self.target,), (), (), False)
        self.handler = Handler(self.profile)
        self.store = Store(self.home / "state")

    def test_clean_records_verified_result_and_filesystem_once(self):
        run = clean([inspect(self.target)], self.profile, self.store, processes=ProcessView)
        self.assertEqual(run["results"][0]["outcome"], "cleaned")
        self.assertEqual(len(run["filesystems"]), 1)
        self.assertFalse(self.file.exists())
        records = self.store.read()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "completed")
        self.assertNotIn("thumbnail" * 20, json.dumps(records))

    def test_command_failure_not_reported_as_success(self):
        with patch.object(self.handler, "execute", return_value=2):
            result = clean_one(inspect(self.target), self.handler, ProcessView)
        self.assertEqual(result.outcome, "failed")
        self.assertTrue(self.file.exists())

    def test_successful_noop_with_payload_is_failed(self):
        with patch.object(self.handler, "execute", return_value=0):
            result = clean_one(inspect(self.target), self.handler, ProcessView)
        self.assertEqual(result.outcome, "failed")

    def test_partial_removal_is_reported(self):
        (self.path / "normal/b.png").write_bytes(b"other" * 1024)

        def partial(*args):
            self.file.unlink()
            return 1

        with patch.object(self.handler, "execute", side_effect=partial):
            result = clean_one(inspect(self.target), self.handler, ProcessView)
        self.assertEqual(result.outcome, "partial")

    def test_blocked_preflight_never_calls_execute(self):
        with patch.object(self.handler, "execute") as execute:
            result = clean_one(
                inspect(self.target), self.handler, lambda: ProcessView(errors=["hidden"])
            )
        execute.assert_not_called()
        self.assertEqual(result.outcome, "skipped")

    def test_preflight_interrupt_is_saved_without_deletion(self):
        with patch.object(self.handler, "preflight", side_effect=KeyboardInterrupt):
            run = clean([inspect(self.target)], self.profile, self.store, self.handler, ProcessView)
        self.assertEqual(run["status"], "interrupted")
        self.assertTrue(self.file.exists())
        self.assertEqual(self.store.read()[0]["results"][0]["outcome"], "interrupted")

    def test_interrupt_saves_current_and_skips_following(self):
        other = self.home / ".cache/Raspberry Pi/Imager"
        other.mkdir(parents=True)
        (other / "lastdownload.cache").write_bytes(b"image")
        second = Target("imager", "imager", other, "desktop", "download")

        def interrupt(*args):
            self.file.unlink()
            raise KeyboardInterrupt

        with patch.object(self.handler, "execute", side_effect=interrupt):
            run = clean(
                [inspect(self.target), inspect(second)],
                self.profile,
                self.store,
                self.handler,
                ProcessView,
            )
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual([r["outcome"] for r in run["results"]], ["interrupted", "skipped"])
        self.assertTrue((other / "lastdownload.cache").exists())
        self.assertEqual(self.store.read()[0]["status"], "interrupted")

    def test_instance_lock_excludes_second_cleanup(self):
        with self.store.lock():
            with self.assertRaisesRegex(TidyError, "already running"):
                with Store(self.store.path).lock():
                    self.fail("second lock acquired")

    def test_history_is_bounded(self):
        with self.store.lock():
            for index in range(52):
                self.store.save({"id": str(index)})
        self.assertEqual(len(self.store.read()), 50)
        self.assertEqual(self.store.read()[0]["id"], "2")

    def test_corrupt_history_blocks_cleanup(self):
        self.store.path.mkdir()
        self.store.file.write_text("broken JSON")
        with self.assertRaises(TidyError):
            clean([inspect(self.target)], self.profile, self.store, processes=ProcessView)
        self.assertTrue(self.file.exists())

    def test_history_symlink_is_rejected(self):
        self.store.path.mkdir()
        secret = self.home / "preserve"
        secret.write_text("[]")
        self.store.file.symlink_to(secret)
        with self.assertRaises(TidyError):
            self.store.read()


if __name__ == "__main__":
    unittest.main()
