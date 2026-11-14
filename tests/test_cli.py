import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.cli import main, selection, size_argument
from workstation_tidy.common import check_environment
from workstation_tidy.models import TidyError
from workstation_tidy.safety import ProcessView
from workstation_tidy.service import clean as real_clean


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = self.home / "config.toml"
        self.config.write_text(
            'version=1\n[profiles.usual]\ncargo_targets=[]\nproject_roots=[]\nreview=false\n[profiles.usual.caches]\nthumbnails="~/.cache/thumbnails"\n'
        )
        self.cache = self.home / ".cache/thumbnails/normal"
        self.cache.mkdir(parents=True)
        self.file = self.cache / "fixture.png"
        self.file.write_bytes(b"preview" * 1024)
        patcher = patch("pathlib.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, args, tty=False, answers=None):
        out = io.StringIO()
        err = io.StringIO()
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            patch.object(out, "isatty", return_value=tty),
            patch("sys.stdin.isatty", return_value=tty),
        ):
            with patch("builtins.input", side_effect=answers or []):
                code = main([*args, "--config", str(self.config)])
        return code, out.getvalue(), err.getvalue()

    def test_json_scan_read_only(self):
        code, out, _ = self.run_cli(["scan", "--json"])
        value = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(len(value["candidates"]), 1)
        self.assertTrue(self.file.exists())
        self.assertFalse((self.home / ".local/state/workstation-tidy").exists())

    def test_doctor_lists_session_daemons_not_inspected(self):
        view = ProcessView(opaque=["PID 3164 ((sd-pam))"])
        with patch("workstation_tidy.cli.process_view", return_value=view):
            code, out, _ = self.run_cli(["doctor", "--json"])
        value = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(value["process_visibility"], "verified")
        self.assertEqual(value["process_not_inspected"], ["PID 3164 ((sd-pam))"])

    def test_clean_preview_names_session_daemons_not_inspected(self):
        view = ProcessView(opaque=["PID 3164 ((sd-pam))"])
        with patch("workstation_tidy.cli.process_view", return_value=view):
            code, out, _ = self.run_cli(["clean"], True, ["1", "no"])
        self.assertEqual(code, 0)
        self.assertIn("Not inspected (session daemons): PID 3164 ((sd-pam))", out)

    def test_show_explains_consequences(self):
        code, out, _ = self.run_cli(["show", "thumbnails", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("regenerated", json.loads(out)["target"]["consequence"])

    def test_noninteractive_cleanup_refused(self):
        code, _, err = self.run_cli(["clean"])
        self.assertEqual(code, 1)
        self.assertIn("interactive terminal", err)
        self.assertTrue(self.file.exists())

    def test_no_selection_does_not_delete(self):
        code, out, _ = self.run_cli(["clean"], True, [""])
        self.assertEqual(code, 0)
        self.assertIn("nothing changed", out)
        self.assertTrue(self.file.exists())

    def test_confirmation_cancel_does_not_execute(self):
        with (
            patch("workstation_tidy.cli.process_view", return_value=ProcessView()),
            patch("workstation_tidy.cli.clean") as clean,
        ):
            code, out, _ = self.run_cli(["clean"], True, ["1", "no"])
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", out)
        clean.assert_not_called()
        self.assertTrue(self.file.exists())

    def test_dry_run_no_cleanup_or_history(self):
        with (
            patch("workstation_tidy.cli.process_view", return_value=ProcessView()),
            patch("workstation_tidy.cli.clean") as clean,
        ):
            code, out, _ = self.run_cli(["clean", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Operation:", out)
        clean.assert_not_called()
        self.assertTrue(self.file.exists())
        self.assertFalse((self.home / ".local/state/workstation-tidy").exists())

    def test_confirmed_fixture_cleanup_and_history(self):
        def fixture_clean(candidates, profile, store):
            return real_clean(candidates, profile, store, processes=ProcessView)

        with (
            patch("workstation_tidy.cli.process_view", return_value=ProcessView()),
            patch("workstation_tidy.cli.clean", side_effect=fixture_clean),
        ):
            code, out, _ = self.run_cli(["clean"], True, ["1", "CLEAN"])
        self.assertEqual(code, 0)
        self.assertIn("CLEANED:", out)
        self.assertFalse(self.file.exists())
        code, out, _ = self.run_cli(["history", "--json"])
        self.assertEqual(json.loads(out)["runs"][0]["results"][0]["outcome"], "cleaned")

    def test_root_refusal(self):
        with patch("os.geteuid", return_value=0):
            code, _, err = self.run_cli(["scan"])
        self.assertEqual(code, 1)
        self.assertIn("without sudo", err)

    def test_size_and_category_filters(self):
        code, out, _ = self.run_cli(["scan", "--min-size", "1GiB", "--json"])
        self.assertEqual(json.loads(out)["candidates"], [])
        self.assertEqual(code, 0)

    def test_older_than_filters_on_newest_mtime(self):
        code, out, _ = self.run_cli(["scan", "--older-than", "90d"])
        self.assertNotIn("thumbnails", out)
        old = time.time() - 100 * 86400
        cache = self.home / ".cache/thumbnails"
        for path in (self.file, self.cache, cache):
            os.utime(path, (old, old))
        code, out, _ = self.run_cli(["scan", "--older-than", "90d"])
        self.assertEqual(code, 0)
        self.assertIn("thumbnails", out)
        self.assertIn("modified", out)

    def test_windows_readonly_scan_does_not_call_geteuid(self):
        with (
            patch("workstation_tidy.common.sys.platform", "win32"),
            patch("workstation_tidy.scanner.sys.platform", "win32"),
            patch("os.geteuid", side_effect=AssertionError("geteuid")),
        ):
            code, out, err = self.run_cli(["scan", "--json"])
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        value = json.loads(out)
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(len(value["candidates"]), 1)
        self.assertIn("newest_mtime", value["candidates"][0]["size"])

    def test_windows_cleanup_is_refused(self):
        with patch("workstation_tidy.common.sys.platform", "win32"):
            code, _, err = self.run_cli(["clean"])
        self.assertEqual(code, 1)
        self.assertIn("scan", err)
        self.assertTrue(self.file.exists())
        with (
            patch("workstation_tidy.common.sys.platform", "win32"),
            patch("os.geteuid", side_effect=AssertionError("geteuid")),
        ):
            check_environment("show")
            with self.assertRaises(TidyError):
                check_environment("tui")

    def test_invalid_selection_and_sizes(self):
        for text in ("0", "2", "all", "1; rm"):
            with self.subTest(text=text), self.assertRaises(TidyError):
                selection(text, 1)
        self.assertEqual(selection("1,1", 1), [0])
        self.assertEqual(size_argument("2GiB"), 2 * 1024**3)


if __name__ == "__main__":
    unittest.main()
