"""Real tool checks, confined to disposable fixture directories."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import Profile
from workstation_tidy.handlers import Handler
from workstation_tidy.models import Target
from workstation_tidy.safety import ProcessView
from workstation_tidy.scanner import inspect


class ToolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="workstation-tidy-tools-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.handler = Handler(Profile("fixture", self.home, (), (), (), False))

    def execute(self, kind, rel):
        target = Target(kind, kind, self.home / rel, "packages", "fixture only")
        candidate = inspect(target)
        command = self.handler.preflight(candidate, ProcessView())
        self.assertEqual(self.handler.execute(candidate, command), 0)
        self.assertFalse(self.handler.has_payload(target))

    @unittest.skipUnless(shutil.which("npm"), "npm unavailable")
    def test_real_npm_cleanup_preserves_npx_and_installed_dependencies(self):
        path = self.home / ".npm/_cacache/content-v2"
        path.mkdir(parents=True)
        (path / "fixture").write_text("downloaded cache")
        npx = self.home / ".npm/_npx/keep"
        npx.parent.mkdir()
        npx.write_text("installed executable")
        modules = self.home / "app/node_modules/keep"
        modules.parent.mkdir(parents=True)
        modules.write_text("installed dependency")
        self.execute("npm", ".npm/_cacache")
        self.assertTrue(npx.exists())
        self.assertTrue(modules.exists())

    @unittest.skipUnless(shutil.which("uv"), "uv unavailable")
    def test_real_uv_cleanup_preserves_environment(self):
        cache = self.home / ".cache/uv"
        (cache / "archive-v0/fixture").mkdir(parents=True)
        (cache / "archive-v0/fixture/package.py").write_text("cache")
        (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
        env = self.home / ".venv/installed.py"
        env.parent.mkdir()
        env.write_text("installed")
        self.execute("uv", ".cache/uv")
        self.assertTrue(env.exists())

    @unittest.skipUnless(shutil.which("pip3"), "pip3 unavailable")
    def test_real_pip_cleanup_preserves_selfcheck(self):
        cache = self.home / ".cache/pip"
        (cache / "wheels/example").mkdir(parents=True)
        (cache / "wheels/example/fixture-0.1.0-py3-none-any.whl").write_bytes(b"fixture")
        (cache / "selfcheck").mkdir()
        (cache / "selfcheck/keep").write_text("metadata")
        self.execute("pip", ".cache/pip")
        self.assertTrue((cache / "selfcheck/keep").exists())

    @unittest.skipUnless(shutil.which("cargo") and shutil.which("git"), "Cargo/git unavailable")
    def test_real_cargo_cleanup_preserves_source_and_lockfile(self):
        project = self.home / "app"
        (project / "src").mkdir(parents=True)
        (project / "Cargo.toml").write_text(
            '[package]\nname="tidy_fixture"\nversion="0.1.0"\nedition="2021"\n'
        )
        source = project / "src/lib.rs"
        source.write_text("pub fn fixture() {}\n")
        lock = project / "Cargo.lock"
        lock.write_text('version = 3\n[[package]]\nname="tidy_fixture"\nversion="0.1.0"\n')
        (project / "target/debug").mkdir(parents=True)
        (project / "target/CACHEDIR.TAG").write_text(
            "Signature: 8a477f597d28d172789f06886806bc55\n"
        )
        (project / "target/debug/fixture").write_text("compiled")
        subprocess.run([shutil.which("git"), "init", "-q", str(project)], check=True)
        with patch.dict(os.environ, {"CARGO_HOME": str(self.home / ".cargo")}):
            self.execute("cargo", "app/target")
        self.assertEqual(source.read_text(), "pub fn fixture() {}\n")
        self.assertTrue(lock.exists())


if __name__ == "__main__":
    unittest.main()
