import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import Profile
from workstation_tidy.handlers import Handler
from workstation_tidy.models import Target, TidyError
from workstation_tidy.safety import (
    Process,
    ProcessView,
    cache_link_check,
    check_cargo_settings,
    process_view,
    validate_identity,
)
from workstation_tidy.scanner import inspect


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.profile = Profile("test", self.home, (), (), (), False)
        self.handler = Handler(self.profile)

    def target(self, rel=".cache/thumbnails", kind="thumbnails"):
        p = self.home / rel
        p.mkdir(parents=True)
        return Target("test", kind, p, "desktop", "regenerate")

    def test_identity_replacement_blocked(self):
        target = self.target()
        candidate = inspect(target)
        target.path.rename(target.path.with_name("old"))
        target.path.mkdir()
        with self.assertRaises(TidyError):
            validate_identity(candidate)

    def test_active_process_or_open_reference_blocks(self):
        target = self.target()
        for process in (Process("other", (target.path / "file",)), Process("cargo")):
            t = (
                target
                if process.name == "other"
                else Target("c", "cargo", target.path, "build", "")
            )
            with self.assertRaises(TidyError):
                ProcessView([process]).check(t)

    def test_process_visibility_blocks_cleanup(self):
        with self.assertRaises(TidyError):
            ProcessView(errors=["private namespace"]).check(self.target())

    def test_npm_process_title_blocks_cleanup(self):
        target = self.target(".npm/_cacache", "npm")
        with self.assertRaises(TidyError):
            ProcessView([Process("npm exec @tool")]).check(target)

    def test_private_namespace_detected(self):
        proc = self.home / "proc"
        (proc / "1").mkdir(parents=True)
        (proc / "1/comm").write_text("codex\n")
        self.assertTrue(process_view(proc).errors)

    def fake_proc(self, pid, comm, argv, fd_mode=0o000, root="proc"):
        """A host-like /proc whose process hides its descriptors like a non-dumpable one."""
        proc = self.home / root
        (proc / "1").mkdir(parents=True, exist_ok=True)
        (proc / "1/comm").write_text("systemd\n")
        directory = proc / str(pid)
        (directory / "fd").mkdir(parents=True)
        (directory / "comm").write_text(comm + "\n")
        (directory / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (directory / "fd/0").symlink_to("/dev/null")
        (directory / "fd").chmod(fd_mode)
        self.addCleanup((directory / "fd").chmod, 0o700)
        return proc

    def test_session_daemons_hiding_descriptors_are_listed_not_blocking(self):
        self.fake_proc(3164, "(sd-pam)", ["(sd-pam)"])
        self.fake_proc(47638, "ssh-agent", ["/usr/bin/ssh-agent", "-D"])
        # systemd --user lists its descriptors but refuses to resolve them.
        proc = self.fake_proc(3148, "systemd", ["/usr/lib/systemd/systemd", "--user"], 0o400)
        view = process_view(proc)
        self.assertEqual(view.errors, [])
        self.assertEqual(
            view.opaque, ["PID 3148 (systemd)", "PID 3164 ((sd-pam))", "PID 47638 (ssh-agent)"]
        )
        view.check(self.target())  # Accepted daemons do not block cleanup.

    def test_unknown_process_hiding_descriptors_blocks(self):
        proc = self.fake_proc(4000, "python3", ["/usr/bin/python3", "build.py"])
        view = process_view(proc)
        self.assertEqual(view.opaque, [])
        with self.assertRaisesRegex(TidyError, "Process visibility insufficient"):
            view.check(self.target())

    def test_session_daemon_name_with_unexpected_command_blocks(self):
        cases = {
            "path": ("ssh-agent", ["/home/user/bin/ssh-agent"]),
            "no-user-flag": ("systemd", ["/usr/lib/systemd/systemd"]),
        }
        for root, (comm, argv) in cases.items():
            with self.subTest(root):
                view = process_view(self.fake_proc(4001, comm, argv, root=root))
                self.assertTrue(view.errors)
                self.assertEqual(view.opaque, [])

    def test_uv_scan_skips_trees_that_cannot_hold_environments(self):
        cache = self.home / ".cache/uv"
        cache.mkdir(parents=True)
        project = self.home / "proj"
        for heavy in ("node_modules", ".git", "__pycache__", "target"):
            (project / heavy).mkdir(parents=True)
            for i in range(30):
                (project / heavy / f"f{i}").touch()
        (project / "Cargo.toml").touch()
        venv = project / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").touch()
        (venv / "python").symlink_to("/usr/bin/python3")
        cache_link_check(cache, self.home, max_entries=40)

    def test_uv_scan_checks_target_dirs_without_a_cargo_manifest(self):
        cache = self.home / ".cache/uv"
        cache.mkdir(parents=True)
        (self.home / "data/target").mkdir(parents=True)
        (self.home / "data/target/pkg").symlink_to(cache / "archive-v0/data")
        with self.assertRaisesRegex(TidyError, "symlinks"):
            cache_link_check(cache, self.home)

    def test_uv_scan_blocks_on_unreadable_directory(self):
        cache = self.home / ".cache/uv"
        cache.mkdir(parents=True)
        locked = self.home / "locked"
        locked.mkdir(mode=0o000)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaisesRegex(TidyError, "Cannot rule out"):
            cache_link_check(cache, self.home)

    def test_uv_environment_symlink_blocks(self):
        cache = self.home / ".cache/uv"
        cache.mkdir(parents=True)
        env = self.home / ".venv"
        env.mkdir()
        (env / "package").symlink_to(cache / "archive-v0/data")
        with self.assertRaisesRegex(TidyError, "symlinks"):
            cache_link_check(cache, self.home)

    def test_uv_inspection_limit_is_not_success(self):
        (self.home / "file").touch()
        with self.assertRaisesRegex(TidyError, "limit"):
            cache_link_check(self.home / "cache", self.home, max_entries=0)

    def test_uv_central_environment_blocks(self):
        cache = self.home / ".cache/uv"
        (cache / "environments-v2/env").mkdir(parents=True)
        with self.assertRaisesRegex(TidyError, "centralized"):
            cache_link_check(cache, self.home)

    def test_cargo_overrides_and_config_are_blocked(self):
        target = self.home / "app/target"
        target.mkdir(parents=True)
        with patch.dict(os.environ, {"CARGO_BUILD_BUILD_DIR": "/outside"}):
            with self.assertRaises(TidyError):
                check_cargo_settings(target, self.home)
        config = self.home / ".cargo/config.toml"
        config.parent.mkdir()
        config.write_text('[build]\nbuild-dir="/outside"\n')
        with self.assertRaises(TidyError):
            check_cargo_settings(target, self.home)

    def test_desktop_removal_preserves_directory_and_outside_data(self):
        target = self.target()
        (target.path / "normal").mkdir()
        (target.path / "normal/a.png").write_bytes(b"thumbnail")
        outside = self.home / "photo.png"
        outside.write_bytes(b"original")
        candidate = inspect(target)
        command = self.handler.preflight(candidate, ProcessView())
        self.assertEqual(self.handler.execute(candidate, command), 0)
        self.assertTrue(outside.exists())
        self.assertTrue((target.path / "normal").is_dir())
        self.assertFalse(self.handler.has_payload(target))

    def test_desktop_unknown_file_blocks_without_deletion(self):
        target = self.target()
        p = target.path / "important.txt"
        p.write_text("preserve")
        with self.assertRaises(TidyError):
            self.handler.preflight(inspect(target), ProcessView())
        self.assertTrue(p.exists())

    def test_imager_only_recognized_download(self):
        target = self.target(".cache/Raspberry Pi/Imager", "imager")
        p = target.path / "lastdownload.cache"
        p.write_bytes(b"image")
        (target.path / "oslistcache0").mkdir()
        metadata = target.path / "oslistcache0/catalog"
        metadata.write_text("retain OS-list metadata")
        candidate = inspect(target)
        self.handler.execute(candidate, self.handler.preflight(candidate, ProcessView()))
        self.assertFalse(p.exists())
        self.assertTrue(metadata.exists())
        self.assertFalse(self.handler.has_payload(target))

    def test_npm_only_passes_parent_cache_and_never_npx(self):
        target = self.target(".npm/_cacache", "npm")
        with patch("shutil.which", return_value="/usr/bin/npm"):
            command = self.handler.preflight(inspect(target), ProcessView())
        self.assertEqual(command[command.index("--cache") + 1], str(target.path.parent))
        self.assertNotIn("_npx", " ".join(command))

    def test_symlink_added_after_preflight_is_not_followed(self):
        target = self.target()
        candidate = inspect(target)
        outside = self.home / "photo.png"
        outside.write_text("keep")
        (target.path / "normal").symlink_to(self.home, target_is_directory=True)
        with self.assertRaises((OSError, TidyError)):
            self.handler.execute(candidate, self.handler.preview(target))
        self.assertEqual(outside.read_text(), "keep")

    def test_tracked_cargo_file_is_blocked(self):
        target = self.target("app/target", "cargo")
        (target.path / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
        (target.path.parent / "Cargo.toml").write_text('[package]\nname="test"\nversion="0.1.0"')
        replies = [
            subprocess.CompletedProcess([], 0, "repo", ""),
            subprocess.CompletedProcess([], 0, "target/file\0", ""),
        ]
        with (
            patch("workstation_tidy.handlers.run_command", side_effect=replies),
            patch("shutil.which", return_value="/bin/git"),
        ):
            with self.assertRaisesRegex(TidyError, "Tracked"):
                self.handler.preflight(inspect(target), ProcessView())

    def test_cargo_command_pins_both_output_directories(self):
        target = self.target("app/target", "cargo")
        with patch("shutil.which", return_value="/usr/bin/cargo"):
            command = self.handler.preview(target)
        self.assertIn("--frozen", command)
        self.assertEqual(command[command.index("--target-dir") + 1], str(target.path))
        self.assertTrue(command[-1].startswith("build.build-dir="))


if __name__ == "__main__":
    unittest.main()
