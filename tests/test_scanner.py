import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import load_profile
from workstation_tidy.models import Target
from workstation_tidy.scanner import discover, inspect, measure


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


if __name__ == "__main__":
    unittest.main()
