from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import CACHE_PATHS, Profile
from .models import Candidate, Target, TidyError
from .safety import (
    ProcessView,
    cache_link_check,
    check_cargo_settings,
    check_launchers,
    validate_identity,
)
from .scanner import inspect


def run_command(
    argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )


class Handler:
    def __init__(self, profile: Profile):
        self.profile = profile

    def discover(self) -> list[Target]:
        return list(self.profile.targets)

    def inspect(self, target: Target) -> Candidate:
        return inspect(target)

    def preflight(self, candidate: Candidate, processes: ProcessView) -> list[str]:
        if not candidate.eligible:
            raise TidyError(candidate.reason)
        target = candidate.target
        validate_identity(candidate)
        if any(
            target.path == p or p in target.path.parents or target.path in p.parents
            for p in self.profile.exclude
        ):
            raise TidyError("Target overlaps an exclusion")
        fresh = self.inspect(target)
        if not fresh.eligible:
            raise TidyError(fresh.reason)
        processes.check(target)
        self.check_layout(target)
        if target.kind == "cargo":
            check_cargo_settings(target.path, self.profile.home)
            check_launchers(target.path, self.profile.home)
        if target.kind == "uv":
            cache_link_check(target.path, self.profile.home)
        return self.preview(target)

    def check_layout(self, target: Target) -> None:
        path = target.path
        names = {p.name for p in path.iterdir()}
        if target.kind in CACHE_PATHS:
            expected = self.profile.home / CACHE_PATHS[target.kind]
            if target.kind in {"thumbnails", "imager"} and path != expected:
                raise TidyError("Desktop cleanup supports only the standard user cache location")
        if target.kind == "npm":
            if path.name != "_cacache" or not names <= {
                "content-v2",
                "index-v5",
                "tmp",
                "CACHEDIR.TAG",
            }:
                raise TidyError("Unrecognized npm cache layout")
            # npm receives the parent directory, so verify its canonical identity too.
            if path.parent.resolve() != path.parent:
                raise TidyError("npm cache parent is not canonical")
        elif target.kind == "uv":
            if "CACHEDIR.TAG" not in names:
                raise TidyError(
                    "Missing uv cache marker; cannot identify this directory as a uv cache"
                )
            if (path / "CACHEDIR.TAG").read_bytes()[
                :43
            ] != b"Signature: 8a477f597d28d172789f06886806bc55":
                raise TidyError("Invalid uv cache marker")
            if any(
                name not in {"CACHEDIR.TAG", ".gitignore", ".lock"}
                and not re.fullmatch(
                    r"(?:archive|wheels|sdists|simple|interpreter|environments|builds|git|flat-index|index|files)-v\d+",
                    name,
                )
                for name in names
            ):
                raise TidyError("Unrecognized uv cache bucket; review before clearing")
        elif target.kind == "pip":
            if not names <= {"http", "http-v2", "wheels", "selfcheck"}:
                raise TidyError("Unrecognized pip cache layout")
        elif target.kind == "cargo":
            self.check_cargo(path, names)
        elif target.kind == "thumbnails":
            if not names <= {"normal", "large", "x-large", "xx-large", "fail"}:
                raise TidyError("Unrecognized thumbnail cache layout")
            for current, dirs, files in os.walk(path, followlinks=False):
                if (
                    Path(current).relative_to(path).parts[:1] != ("fail",)
                    and Path(current) != path
                    and dirs
                ):
                    raise TidyError("Unexpected nested thumbnail directory")
                for name in files:
                    if not name.endswith(".png"):
                        raise TidyError("Non-thumbnail file in cache; review required")
        elif target.kind == "imager":
            if any(
                name != "lastdownload.cache" and not re.fullmatch(r"oslistcache\d+", name)
                for name in names
            ):
                raise TidyError(
                    "Unsupported Imager layout; only download and OS-list metadata are recognized"
                )
            if "lastdownload.cache" in names and not (path / "lastdownload.cache").is_file():
                raise TidyError("Imager download is not a regular file")
            if any(name != "lastdownload.cache" and not (path / name).is_dir() for name in names):
                raise TidyError("Unexpected Imager OS-list metadata layout")
        else:
            raise TidyError("Report-only target; no cleanup handler")

    def check_cargo(self, path: Path, names: set[str]) -> None:
        manifest = path.parent / "Cargo.toml"
        if path.name != "target" or manifest.is_symlink() or not manifest.is_file():
            raise TidyError("Expected target beside a regular Cargo.toml")
        allowed = {
            ".rustc_info.json",
            "CACHEDIR.TAG",
            ".rustdoc_fingerprint.json",
            "debug",
            "release",
            "doc",
            "package",
            "tmp",
            "rls",
            "nextest",
        }
        for name in names - allowed:
            # Rust target triples contain at least two hyphens.
            if name.count("-") < 2 or not (path / name).is_dir():
                raise TidyError("Unrecognized file in build output; preserve for review")
        if not ({".rustc_info.json", "CACHEDIR.TAG"} & names):
            raise TidyError("No Cargo output marker; generated contents cannot be verified")
        if (
            "CACHEDIR.TAG" in names
            and (path / "CACHEDIR.TAG").read_bytes()[:43]
            != b"Signature: 8a477f597d28d172789f06886806bc55"
        ):
            raise TidyError("Invalid Cargo cache marker; preserve for review")
        for current, _, files in os.walk(path, followlinks=False):
            if "Cargo.toml" in files or "Cargo.lock" in files:
                raise TidyError("Project files found inside build output")
        git = shutil.which("git")
        if not git:
            raise TidyError("git is required to check tracked files before Cargo cleanup")
        repo = run_command(
            [git, "-C", str(path.parent), "rev-parse", "--show-toplevel"], cwd=path.parent
        )
        if repo.returncode:
            raise TidyError("Cargo project is not in an inspectable Git repository")
        tracked = run_command(
            [git, "-C", str(path.parent), "ls-files", "-z", "--", "target"], cwd=path.parent
        )
        if tracked.returncode or tracked.stdout:
            raise TidyError("Tracked output files or Git inspection failure; cleanup blocked")

    def preview(self, target: Target) -> list[str]:
        path = str(target.path)
        if target.kind == "npm":
            return [
                self.tool("npm"),
                "cache",
                "clean",
                "--force",
                "--cache",
                str(target.path.parent),
                "--logs-max=0",
                "--update-notifier=false",
            ]
        if target.kind == "uv":
            return [
                self.tool("uv"),
                "--no-config",
                "--offline",
                "cache",
                "clean",
                "--cache-dir",
                path,
            ]
        if target.kind == "pip":
            binary = shutil.which("pip3") or shutil.which("pip")
            if binary:
                prefix = [binary]
            elif importlib.util.find_spec("pip") is not None:
                prefix = [sys.executable, "-m", "pip"]
            else:
                raise TidyError("pip is unavailable in this Python interpreter")
            return [
                *prefix,
                "--isolated",
                "--disable-pip-version-check",
                "--cache-dir",
                path,
                "cache",
                "purge",
            ]
        if target.kind == "cargo":
            # The override pins both artifact and intermediate directories even
            # on newer Cargo versions that support a separate build directory.
            return [
                self.tool("cargo"),
                "clean",
                "--frozen",
                "--manifest-path",
                str(target.path.parent / "Cargo.toml"),
                "--target-dir",
                path,
                "--config",
                "build.build-dir=" + json.dumps(path),
            ]
        if target.kind in {"thumbnails", "imager"}:
            return ["internal:unlink-recognized-cache-files", path]
        raise TidyError("Report-only target")

    @staticmethod
    def tool(name: str) -> str:
        value = shutil.which(name)
        if not value:
            raise TidyError(f"Required tool is missing: {name}")
        return value

    def execute(self, candidate: Candidate, command: list[str]) -> int:
        validate_identity(candidate)
        self.check_layout(candidate.target)
        if candidate.target.kind in {"thumbnails", "imager"}:
            self.unlink_desktop(candidate)
            return 0
        # Remove unrelated package-manager configuration from the child, and
        # avoid writing npm logs outside the selected cache.
        env = dict(os.environ)
        if candidate.target.kind == "npm":
            env = {k: v for k, v in env.items() if not k.lower().startswith("npm_config_")}
            # npm rejects loading the same file as both user and global config.
            with tempfile.TemporaryDirectory(prefix="workstation-tidy-npm-") as directory:
                for name in ("user", "global"):
                    path = Path(directory) / name
                    path.touch()
                    env[f"NPM_CONFIG_{name.upper()}CONFIG"] = str(path)
                return run_command(command, cwd=Path(directory), env=env).returncode
        result = run_command(command, cwd=candidate.target.path.parent, env=env)
        return result.returncode

    @staticmethod
    def unlink_desktop(candidate: Candidate) -> None:
        path = candidate.target.path
        root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        def unlink_tree(fd: int, device: int) -> None:
            for name in os.listdir(fd):
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if entry.st_dev != device or entry.st_uid != os.getuid():
                    raise TidyError("Cache ownership or filesystem changed")
                if stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        unlink_tree(child, device)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(entry.st_mode):
                    allowed = (
                        name == "lastdownload.cache"
                        if candidate.target.kind == "imager"
                        else name.endswith(".png")
                    )
                    if not allowed:
                        raise TidyError("Unexpected cache file appeared; stopped")
                    os.unlink(name, dir_fd=fd)
                else:
                    raise TidyError("Cache entry changed; stopped")

        try:
            info = os.fstat(root)
            if (info.st_dev, info.st_ino) != candidate.identity:
                raise TidyError("Cache directory identity changed")
            if candidate.target.kind == "imager":
                # Keep OS-list metadata; only the downloaded image is selected.
                try:
                    entry = os.stat("lastdownload.cache", dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    return
                if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid():
                    raise TidyError("Imager cache file changed; stopped")
                os.unlink("lastdownload.cache", dir_fd=root)
            else:
                unlink_tree(root, info.st_dev)
        finally:
            os.close(root)

    @staticmethod
    def has_payload(target: Target) -> bool:
        if not target.path.exists():
            return False
        if target.kind == "imager":
            return os.path.lexists(target.path / "lastdownload.cache")
        ignored = {"CACHEDIR.TAG", ".lock", ".gitignore"}
        for current, dirs, files in os.walk(target.path, followlinks=False):
            if target.kind == "pip" and Path(current) == target.path:
                dirs[:] = [d for d in dirs if d != "selfcheck"]
            if any(name not in ignored for name in files):
                return True
            if any((Path(current) / d).is_symlink() for d in dirs):
                return True
        return False
