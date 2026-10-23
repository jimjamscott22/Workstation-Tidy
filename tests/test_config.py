import tempfile
import unittest
from pathlib import Path

from workstation_tidy.config import load_profile
from workstation_tidy.models import TidyError
from workstation_tidy.scanner import scan


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.path = self.home / "config.toml"

    def load(self, body):
        self.path.write_text("version=1\n[profiles.test]\nreview=false\n" + body)
        return load_profile("test", self.path, self.home)

    def test_commands_not_allowed(self):
        with self.assertRaises(TidyError):
            self.load('command="rm -rf anything"')

    def test_unknown_profile_fails(self):
        with self.assertRaises(TidyError):
            load_profile("unknown", self.home / "missing", self.home)

    def test_duplicate_and_overlap_rejected(self):
        for values in (
            '["~/app/target", "~/app/target"]',
            '["~/app/target", "~/app/target/sub/target"]',
        ):
            with self.subTest(values=values), self.assertRaises(TidyError):
                self.load(f"cargo_targets={values}\n[caches]\n")

    def test_paths_outside_home_rejected(self):
        with self.assertRaises(TidyError):
            self.load('cargo_targets=["/var/lib/target"]')

    def test_excluded_child_blocks_whole_target(self):
        target = self.home / "app/target"
        target.mkdir(parents=True)
        profile = self.load(
            'cargo_targets=["~/app/target"]\nexclude=["~/app/target/keep"]\n[profiles.test.caches]\n'
        )
        candidates, _ = scan(profile)
        self.assertFalse(candidates[0].eligible)
        self.assertIn("excluded", candidates[0].reason)

    def test_custom_profile_cache_and_exclusions(self):
        profile = self.load(
            'cargo_targets=[]\nexclude=["~/cache/_cacache"]\n[profiles.test.caches]\nnpm="~/cache/_cacache"\nuv=false\n'
        )
        self.assertEqual(profile.targets, ())


if __name__ == "__main__":
    unittest.main()
