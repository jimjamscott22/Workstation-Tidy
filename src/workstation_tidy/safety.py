from __future__ import annotations

import os
import stat
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import Candidate, Target, TidyError
from .scanner import path_identity


@dataclass
class Process:
    name: str
    references: tuple[Path, ...] = ()


@dataclass
class ProcessView:
    processes: list[Process] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def check(self, target: Target) -> None:
        if self.errors:
            raise TidyError("Process visibility insufficient: " + "; ".join(self.errors))
        active = {
            "cargo": {"cargo", "rustc", "rustrover"},
            "npm": {"npm", "npx"},
            "uv": {"uv", "uvx"},
            "pip": {"pip", "pip3"},
            "thumbnails": {"gnome-desktop-thumbnailer", "tumblerd"},
            "imager": {"rpi-imager", "imager"},
        }.get(target.kind, set())
        for process in self.processes:
            if process.name.lower().split()[0] in active or any(
                p == target.path or target.path in p.parents for p in process.references
            ):
                raise TidyError(f"Target may be in use by {process.name}; close it and scan again")


def process_view(proc: Path = Path("/proc")) -> ProcessView:
    view = ProcessView()
    try:
        # A sandbox's private PID namespace cannot establish host inactivity.
        if (proc / "1/comm").read_text().strip() not in {"systemd", "init"}:
            view.errors.append("host process namespace is not visible")
            return view
        pids = list(proc.iterdir())
    except OSError as exc:
        view.errors.append(str(exc))
        return view
    for directory in pids:
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            name = (directory / "comm").read_text().strip()
            # Only classify command names; arguments are never stored or printed.
            argv = (directory / "cmdline").read_bytes().split(b"\0")[:3]
            for arg in argv:
                stem = Path(os.fsdecode(arg)).name
                if stem in {"npm-cli.js", "npx-cli.js"}:
                    name = "npm"
                if stem == "pip" and b"-m" in argv:
                    name = "pip"
            refs = []
            entries = [directory / "cwd", directory / "exe", *list((directory / "fd").iterdir())]
            for entry in entries:
                try:
                    dest = os.readlink(entry).removesuffix(" (deleted)")
                    if dest.startswith("/"):
                        refs.append(Path(dest))
                except FileNotFoundError:
                    continue  # Processes and descriptors can exit during inspection.
                except PermissionError:
                    view.errors.append(f"cannot inspect references of PID {directory.name}")
            view.processes.append(Process(name, tuple(refs)))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            view.errors.append(f"cannot inspect PID {directory.name}")
    view.errors = sorted(set(view.errors))
    return view


def validate_identity(candidate: Candidate) -> None:
    try:
        if candidate.identity is None or path_identity(candidate.target.path) != candidate.identity:
            raise TidyError("Target identity changed; scan again")
    except (OSError, RuntimeError) as exc:
        raise TidyError(f"Target changed or inaccessible: {exc}") from exc


def check_cargo_settings(target: Path, home: Path) -> None:
    for key in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET_DIR", "CARGO_BUILD_BUILD_DIR"):
        if os.environ.get(key):
            raise TidyError(f"Cargo directory override {key} is set; unset it before cleanup")
    paths = {home / ".cargo"}
    cargo_home = os.environ.get("CARGO_HOME")
    if cargo_home:
        paths.add(Path(cargo_home))
    for parent in (target.parent, *target.parent.parents):
        paths.add(parent / ".cargo")
    for directory in paths:
        for name in ("config", "config.toml"):
            path = directory / name
            if not path.exists():
                continue
            if path.is_symlink():
                raise TidyError(f"Symlinked Cargo config requires review: {path}")
            try:
                cfg = tomllib.loads(path.read_text())
                build = cfg.get("build", {})
                if (
                    "include" in cfg
                    or "env" in cfg
                    or "build-dir" in build
                    or "target-dir" in build
                ):
                    raise TidyError(f"Cargo configuration may broaden cleanup; review {path}")
            except (OSError, tomllib.TOMLDecodeError, TypeError) as exc:
                raise TidyError(f"Cannot verify Cargo configuration: {path}") from exc


def cache_link_check(
    cache: Path, home: Path, max_entries: int = 500_000, seconds: float = 20.0
) -> None:
    """Look for environments symlinked into uv's cache using metadata only.

    A bounded or incomplete traversal cannot prove safety and blocks cleanup.
    Cache-internal symlinks are irrelevant to external environment dependencies.
    """
    deadline = time.monotonic() + seconds
    count = 0

    def error(exc: OSError) -> None:
        raise TidyError(f"Cannot rule out uv cache dependencies: {exc}")

    for current, dirs, files in os.walk(home, followlinks=False, onerror=error):
        path = Path(current)
        if path == cache:
            dirs[:] = []
            continue
        for name in dirs + files:
            count += 1
            if count > max_entries or time.monotonic() > deadline:
                raise TidyError("uv dependency inspection limit reached; cache cleanup deferred")
            p = path / name
            try:
                info = p.lstat()
                if stat.S_ISLNK(info.st_mode):
                    dest = p.resolve(strict=False)
                    if dest == cache or cache in dest.parents:
                        raise TidyError(
                            "An environment symlinks into the uv cache; cleanup deferred"
                        )
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError) as exc:
                raise TidyError("Cannot rule out uv cache symlink dependencies") from exc
    # uv may maintain centralized project environments; do not delete those as downloads.
    for name in ("environments-v2", "environments-v1"):
        p = cache / name
        if p.exists() and any(p.iterdir()):
            raise TidyError(
                "uv centralized environments present; review before clearing the whole cache"
            )


def check_launchers(target: Path, home: Path) -> None:
    for directory in (home / ".local/share/applications", home / "Desktop"):
        if not directory.exists():
            continue
        try:
            for p in directory.glob("*.desktop"):
                if p.is_symlink():
                    raise TidyError(
                        "Symlinked desktop launcher requires review before Cargo cleanup"
                    )
                text = p.read_text(errors="replace")
                if str(target) in text or str(target).replace(str(home), "~", 1) in text:
                    raise TidyError(f"A desktop launcher references this build output: {p.name}")
        except OSError as exc:
            raise TidyError("Cannot inspect desktop launchers") from exc
