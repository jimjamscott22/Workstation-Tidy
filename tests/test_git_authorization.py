"""Real Git authorization regressions; every path belongs to a disposable home."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.config import Profile
from workstation_tidy.handlers import Handler
from workstation_tidy.models import Target, TidyError
from workstation_tidy.safety import ProcessView
from workstation_tidy.scanner import inspect


@unittest.skipUnless(shutil.which("git"), "Git unavailable")
class GitAuthorizationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tidy-git-authorization-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        # Strip developer Git overrides when creating fixtures; inject them only
        # around the authorization being tested.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        clean.update(
            HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            XDG_CACHE_HOME=str(self.home / "cache"),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
        )
        scope = patch.dict(os.environ, clean, clear=True)
        scope.start()
        self.addCleanup(scope.stop)
        self.handler = Handler(Profile("fixture", self.home, (), (), (), False))

    def git(self, project, *args):
        return subprocess.run(
            ["git", "-C", str(project), *args], check=True, capture_output=True, text=True
        )

    def fixture(self, kind, project=None):
        project = project or self.home / kind
        project.mkdir(parents=True, exist_ok=True)
        if not (project / ".git").exists():
            self.git(project, "init", "-q")
        name = "target" if kind == "cargo" else "node_modules"
        folder = project / name
        folder.mkdir(exist_ok=True)
        if kind == "cargo":
            (project / "Cargo.toml").write_text(
                '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
            )
            (project / "src").mkdir(exist_ok=True)
            (project / "src/lib.rs").write_text("pub fn fixture() {}\n")
            (folder / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
            folder.joinpath("debug").mkdir(exist_ok=True)
        else:
            (project / "package.json").write_text("{}\n")
            (project / "package-lock.json").write_text("{}\n")
        return Target(kind, kind, folder, "build", "fixture only")

    def test_inherited_git_overrides_cannot_hide_tracked_files(self):
        other = self.home / "unrelated"
        other.mkdir()
        self.git(other, "init", "-q")
        config = self.home / "injected.gitconfig"
        config.write_text(f"[core]\nworktree = {other}\n")
        overrides = [
            {"GIT_CONFIG_GLOBAL": str(config)},
            {"GIT_CONFIG_SYSTEM": str(config)},
            {"GIT_INDEX_FILE": str(self.home / "empty-index")},
            {"GIT_DIR": str(other / ".git")},
            {"GIT_WORK_TREE": str(other)},
            {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)},
            {"GIT_COMMON_DIR": str(other / ".git")},
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.worktree",
                "GIT_CONFIG_VALUE_0": str(other),
            },
            {"GIT_CONFIG_PARAMETERS": f"'core.worktree={other}'"},
        ]
        for kind in ("node_modules", "cargo"):
            target = self.fixture(kind)
            source = target.path / ("debug/source.txt" if kind == "cargo" else "source.txt")
            source.write_text("tracked source\n")
            self.git(target.path.parent, "add", str(source))
            for override in overrides:
                with self.subTest(kind=kind, override=next(iter(override))):
                    with patch.dict(os.environ, override):
                        with self.assertRaisesRegex(TidyError, "Tracked"):
                            self.handler.check_layout(target)
                        with self.assertRaisesRegex(TidyError, "Tracked"):
                            self.handler.execute(inspect(target), ["unused"])
                    self.assertEqual(source.read_text(), "tracked source\n")

    def test_normal_untracked_project_and_nested_project_are_allowed(self):
        target = self.fixture("node_modules")
        self.handler.check_layout(target)
        nested = target.path.parent / "packages/subproject"
        nested.mkdir(parents=True)
        # Fixture helper initially initializes its own repo; remove that empty
        # metadata to exercise discovery of the ancestor worktree instead.
        nested_target = self.fixture("node_modules", nested)
        shutil.rmtree(nested / ".git")
        self.handler.check_layout(nested_target)
        source = nested_target.path / "source.txt"
        source.write_text("tracked\n")
        self.git(nested, "add", str(source))
        with self.assertRaisesRegex(TidyError, "Tracked"):
            self.handler.check_layout(nested_target)

    def test_linked_worktree_is_allowed_and_its_index_is_checked(self):
        repo = self.home / "repository"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(
            repo,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        )
        worktree = self.home / "linked"
        self.git(repo, "worktree", "add", "-qb", "fixture", str(worktree))
        target = self.fixture("node_modules", worktree)
        self.handler.check_layout(target)
        (target.path / "tracked").write_text("preserve")
        self.git(worktree, "add", "node_modules/tracked")
        with self.assertRaisesRegex(TidyError, "Tracked"):
            self.handler.check_layout(target)

    def test_unrelated_repository_root_and_git_failure_are_rejected(self):
        target = self.fixture("node_modules")
        for reply in (
            subprocess.CompletedProcess([], 0, str(self.home / "elsewhere") + "\n", ""),
            subprocess.CompletedProcess([], 0, "relative/root\n", ""),
            subprocess.CompletedProcess([], 1, "", "failed"),
        ):
            with self.subTest(stdout=reply.stdout, code=reply.returncode):
                with patch("workstation_tidy.handlers.run_command", return_value=reply):
                    with self.assertRaises(TidyError):
                        self.handler.check_layout(target)

    def test_local_config_cannot_redirect_inspection_to_unrelated_worktree(self):
        target = self.fixture("node_modules")
        other = self.home / "other-worktree"
        other.mkdir()
        self.git(target.path.parent, "config", "core.worktree", str(other))
        with self.assertRaisesRegex(TidyError, "worktree"):
            self.handler.check_layout(target)

    def test_git_index_failure_is_rejected(self):
        target = self.fixture("node_modules")
        replies = [
            subprocess.CompletedProcess([], 0, str(target.path.parent) + "\n", ""),
            subprocess.CompletedProcess([], 1, "", "cannot read index"),
        ]
        with patch("workstation_tidy.handlers.run_command", side_effect=replies):
            with self.assertRaisesRegex(TidyError, "Git inspection failure"):
                self.handler.check_layout(target)

    def test_nested_repositories_are_rejected_by_both_handlers(self):
        for kind in ("node_modules", "cargo"):
            for layout in ("directory", "gitfile", "bare"):
                with self.subTest(kind=kind, layout=layout):
                    target = self.fixture(kind, self.home / f"{kind}-{layout}")
                    nested = target.path / "debug/vendor"
                    nested.mkdir(parents=True)
                    if layout == "directory":
                        self.git(nested, "init", "-q")
                        (nested / "source.py").write_text("local source")
                        self.git(nested, "add", "source.py")
                        self.git(
                            nested,
                            "-c",
                            "user.name=Fixture",
                            "-c",
                            "user.email=fixture@example.invalid",
                            "commit",
                            "-qm",
                            "local only",
                        )
                    elif layout == "gitfile":
                        (nested / ".git").write_text("gitdir: /fixture/unavailable\n")
                    else:
                        self.git(nested, "init", "--bare", "-q")
                    with self.assertRaisesRegex(TidyError, "Nested Git"):
                        self.handler.check_layout(target)
                    self.assertTrue(nested.exists())
                    if layout == "directory":
                        self.assertEqual((nested / "source.py").read_text(), "local source")

    def test_unreadable_cargo_descendant_blocks_layout_validation(self):
        target = self.fixture("cargo")
        hidden = target.path / "debug/hidden"
        hidden.mkdir()
        hidden.chmod(0)
        try:
            with self.assertRaisesRegex(TidyError, "nested Git"):
                self.handler.check_layout(target)
        finally:
            hidden.chmod(0o700)

    @unittest.skipUnless(shutil.which("cargo"), "Cargo unavailable")
    def test_cargo_rechecks_nested_repository_after_preflight(self):
        target = self.fixture("cargo")
        candidate = inspect(target)
        command = self.handler.preflight(candidate, ProcessView())
        nested = target.path / "debug/new-repository"
        nested.mkdir()
        self.git(nested, "init", "-q")
        source = nested / "source.py"
        source.write_text("preserve local work")
        with self.assertRaisesRegex(TidyError, "Nested Git"):
            self.handler.execute(candidate, command)
        self.assertEqual(source.read_text(), "preserve local work")
        self.assertTrue((nested / ".git").exists())


if __name__ == "__main__":
    unittest.main()
