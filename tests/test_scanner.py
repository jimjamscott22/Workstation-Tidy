import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import load_profile
from workstation_tidy.models import Target
from workstation_tidy.scanner import discover, discover_artifacts, inspect, measure, path_identity


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_hardlinks_count_once_and_sparse_size_is_allocated(self):
        p = self.root / "sparse"
        with p.open("wb") as f:
            f.truncate(16 * 1024 * 1024)
        os.link(p, self.root / "hardlink")
        result = measure(self.root)
        self.assertEqual(result.files, 1)
        self.assertLess(result.allocated_bytes, result.logical_bytes)
        self.assertEqual(result.logical_bytes, 16 * 1024 * 1024)

    def test_symlink_not_followed(self):
        outside = self.root / "outside"
        outside.write_bytes(b"private" * 1024)
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "link").symlink_to(outside)
        result = measure(cache)
        self.assertEqual(result.files, 0)
        self.assertTrue(result.errors)

    def test_target_symlink_is_blocked(self):
        p = self.root / "link"
        p.symlink_to(self.root, target_is_directory=True)
        candidate = inspect(Target("x", "npm", p, "packages", "download again"))
        self.assertFalse(candidate.eligible)

    def test_discovery_finds_worktree_but_not_dependencies(self):
        for rel in ("app", "app/.worktrees/feature", "app/node_modules/dependency"):
            p = self.root / rel
            p.mkdir(parents=True, exist_ok=True)
            (p / "Cargo.toml").write_text('[package]\nname="test"\nversion="0.1.0"\n')
            (p / "target").mkdir()
        targets, errors = discover((self.root,), ())
        self.assertEqual(len(targets), 2)
        self.assertFalse(errors)

    def test_missing_target_is_reported(self):
        candidate = inspect(Target("x", "cargo", self.root / "missing", "build", ""))
        self.assertFalse(candidate.eligible)
        self.assertIn("Missing", candidate.reason)

    def test_permission_failure_is_reported_as_unknown(self):
        with patch("workstation_tidy.scanner.os.open", side_effect=PermissionError("denied")):
            measured = measure(self.root)
        self.assertTrue(measured.errors)

    def test_uv_internal_links_are_not_followed_or_double_counted(self):
        (self.root / "archive-v0").mkdir()
        (self.root / "archive-v0/package").write_bytes(b"payload" * 1024)
        (self.root / "wheels-v6").mkdir()
        (self.root / "wheels-v6/link").symlink_to("../archive-v0/package")
        result = measure(self.root, allow_internal_links=True)
        self.assertEqual(result.files, 1)
        self.assertFalse(result.errors)

    def test_uv_external_links_are_blocked(self):
        (self.root / "link").symlink_to("/etc")
        self.assertTrue(measure(self.root, allow_internal_links=True).errors)

    def test_default_profile_has_eight_cargo_targets(self):
        p = load_profile(path=self.root / "none.toml", home=self.root)
        self.assertEqual(sum(t.kind == "cargo" for t in p.targets), 8)
        self.assertTrue(p.discover_artifacts)
        self.assertFalse(p.project_roots_explicit)

    def test_missing_default_root_is_not_a_scan_failure(self):
        from workstation_tidy.scanner import scan

        profile = load_profile(path=self.root / "none.toml", home=self.root)
        _candidates, errors = scan(profile)
        self.assertEqual(errors, [])

    def test_explicit_missing_project_root_is_reported(self):
        from workstation_tidy.scanner import scan

        config = self.root / "config.toml"
        config.write_text(
            "version=1\n[profiles.test]\nreview=false\ncargo_targets=[]\n"
            'project_roots=["~/missing-projects"]\n[profiles.test.caches]\n'
        )
        profile = load_profile("test", config, self.root)
        _candidates, errors = scan(profile)
        self.assertTrue(any("missing" in error for error in errors))

    def test_artifact_discovery_recognizes_regenerable_folders(self):
        app = self.root / "app"
        nested = app / "node_modules" / "pkg"
        for path in (
            app,
            nested,
            app / ".venv",
            app / "venv",
            app / ".pytest_cache",
            app / ".next",
            app / "dist",
            app / "__pycache__",
            app / "notes" / "out",
        ):
            path.mkdir(parents=True)
        (app / "package.json").write_text("{}\n")
        (app / "package-lock.json").write_text("{}\n")
        (app / "pyproject.toml").write_text("[project]\nname='app'\n")
        (app / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
        (nested / "package.json").write_text("{}\n")
        (nested / "package-lock.json").write_text("{}\n")
        (nested / "node_modules").mkdir()
        (app / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
        bare = self.root / "bare"
        (bare / "node_modules").mkdir(parents=True)
        (bare / "package.json").write_text("{}\n")
        targets, errors = discover_artifacts((self.root,), (), missing_ok=False)
        by_path = {target.path: target for target in targets}
        self.assertEqual(errors, [])
        self.assertEqual(by_path[app / "node_modules"].kind, "node_modules")
        self.assertIn("package-lock.json", by_path[app / "node_modules"].detail)
        self.assertIn("no lockfile", by_path[bare / "node_modules"].detail)
        self.assertEqual(by_path[app / ".venv"].kind, "venv")
        self.assertEqual(by_path[app / ".pytest_cache"].kind, "tool_cache")
        self.assertEqual(by_path[app / ".next"].kind, "frontend_cache")
        self.assertEqual(by_path[app / "dist"].kind, "review")
        self.assertNotIn(app / "venv", by_path)
        self.assertNotIn(app / "__pycache__", by_path)
        self.assertNotIn(app / "notes" / "out", by_path)
        self.assertEqual(sum(target.path.name == "node_modules" for target in targets), 2)

    def test_node_modules_symlinks_do_not_block_or_follow(self):
        modules = self.root / "node_modules"
        modules.mkdir()
        outside = self.root / "python"
        outside.write_bytes(b"interpreter")
        (modules / "python").symlink_to(outside)
        (modules / "pkg.js").write_bytes(b"code")
        target = Target(
            "artifact-test",
            "node_modules",
            modules,
            "build",
            "reinstall",
            f"project={self.root}; marker=package-lock.json",
        )
        candidate = inspect(target)
        self.assertTrue(candidate.eligible)
        self.assertFalse(candidate.size.errors)
        self.assertEqual(candidate.size.files, 1)
        self.assertEqual(candidate.size.logical_bytes, len(b"code"))
        self.assertIsNotNone(candidate.size.newest_mtime)

    def test_symlinked_artifact_root_is_blocked(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "node_modules"
        link.symlink_to(real, target_is_directory=True)
        (self.root / "package.json").write_text("{}\n")
        (self.root / "package-lock.json").write_text("{}\n")
        targets, errors = discover_artifacts((self.root,), (), missing_ok=False)
        self.assertEqual(errors, [])
        self.assertEqual([target.path for target in targets], [link])
        self.assertFalse(inspect(targets[0]).eligible)

    def test_windows_measure_uses_real_stat_when_direntry_identity_is_zero(self):
        (self.root / "a").write_bytes(b"one")
        os.link(self.root / "a", self.root / "a-link")
        (self.root / "b").write_bytes(b"two")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "c").write_bytes(b"three")

        def zero_identity(entry, *, follow_symlinks=True):
            real = os.stat(entry.path, follow_symlinks=follow_symlinks)
            values = list(real)
            values[1] = 0
            values[2] = 0
            return os.stat_result(tuple(values))

        with (
            patch("workstation_tidy.scanner.sys.platform", "win32"),
            patch("os.DirEntry.stat", zero_identity),
        ):
            result = measure(self.root, keep_links=True)
        self.assertFalse(any("Nested filesystem" in error for error in result.errors))
        self.assertEqual(result.files, 3)
        self.assertEqual(result.logical_bytes, len(b"onetwothree"))

    def test_windows_measure_skips_owner_checks_and_keeps_links(self):
        (self.root / "file").write_bytes(b"abc")
        (self.root / "link").symlink_to(self.root / "file")
        with (
            patch("workstation_tidy.scanner.sys.platform", "win32"),
            patch("workstation_tidy.scanner.os.getuid", side_effect=AssertionError("getuid")),
        ):
            result = measure(self.root, keep_links=True)
            identity = path_identity(self.root)
        self.assertFalse(result.errors)
        self.assertEqual(result.files, 1)
        self.assertEqual(identity, (self.root.lstat().st_dev, self.root.lstat().st_ino))


if __name__ == "__main__":
    unittest.main()
