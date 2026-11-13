from __future__ import annotations

import os
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


# Session daemons that mark themselves non-dumpable, so even their owner cannot read
# their descriptors. Each is accepted only when its command line also matches, and is
# always reported as not inspected; any other unreadable process still blocks cleanup.
SESSION_DAEMONS: dict[str, tuple[tuple[str, ...], ...]] = {
    "(sd-pam)": (("(sd-pam)",),),
    "systemd": (("/usr/lib/systemd/systemd", "--user"), ("/lib/systemd/systemd", "--user")),
    "ssh-agent": (("/usr/bin/ssh-agent",),),
    "gpg-agent": (("/usr/bin/gpg-agent",),),
    "gnome-keyring-d": (("/usr/bin/gnome-keyring-daemon",),),
}


def session_daemon(comm: str, argv: list[bytes]) -> bool:
    args = [os.fsdecode(a) for a in argv if a]
    return any(args[: len(prefix)] == list(prefix) for prefix in SESSION_DAEMONS.get(comm, ()))


@dataclass
class ProcessView:
    processes: list[Process] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    opaque: list[str] = field(default_factory=list)

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
            comm = name = (directory / "comm").read_text().strip()
            # Only classify command names; arguments are never stored or printed.
            argv = (directory / "cmdline").read_bytes().split(b"\0")[:3]
            for arg in argv:
                stem = Path(os.fsdecode(arg)).name
                if stem in {"npm-cli.js", "npx-cli.js"}:
                    name = "npm"
                if stem == "pip" and b"-m" in argv:
                    name = "pip"
            refs = []
            denied = False
            try:
                descriptors = list((directory / "fd").iterdir())
            except PermissionError:
                descriptors, denied = [], True
            for entry in [directory / "cwd", directory / "exe", *descriptors]:
                try:
                    dest = os.readlink(entry).removesuffix(" (deleted)")
                    if dest.startswith("/"):
                        refs.append(Path(dest))
                except FileNotFoundError:
                    continue  # Processes and descriptors can exit during inspection.
                except PermissionError:
                    denied = True
            if denied:
                if session_daemon(comm, argv):
                    view.opaque.append(f"PID {directory.name} ({comm})")
                else:
                    view.errors.append(f"cannot inspect references of PID {directory.name}")
            view.processes.append(Process(name, tuple(refs)))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            view.errors.append(f"cannot inspect PID {directory.name}")
    view.errors = sorted(set(view.errors))
    view.opaque.sort(key=lambda entry: int(entry.split()[1]))
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


# Only trees whose layout is fixed by their tool and which cannot contain a Python
# environment: git's object store and bytecode caches. Build/dependency directories
# such as "target" or "node_modules" are scanned, since a project may nest an
# environment inside them; a scan that runs out of budget defers cleanup instead.
UV_SCAN_PRUNE = frozenset({".git", "__pycache__"})


def cache_link_check(
    cache: Path, home: Path, max_entries: int = 3_000_000, seconds: float = 20.0
) -> None:
    """Look for environments symlinked into uv's cache using metadata only.

    A bounded or incomplete traversal cannot prove safety and blocks cleanup.
    Cache-internal symlinks are irrelevant to external environment dependencies.
    Entry types come from directory listings, so only symlinks cost an extra call.
    """
    deadline = time.monotonic() + seconds
    count = 0
    pending = [home]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as listing:
                entries = list(listing)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise TidyError(f"Cannot rule out uv cache dependencies: {exc}") from exc
        for entry in entries:
            count += 1
            if count > max_entries or time.monotonic() > deadline:
                raise TidyError("uv dependency inspection limit reached; cache cleanup deferred")
            try:
                if entry.is_symlink():
                    dest = Path(entry.path).resolve(strict=False)
                    if dest == cache or cache in dest.parents:
                        raise TidyError(
                            "An environment symlinks into the uv cache; cleanup deferred"
                        )
                elif entry.is_dir(follow_symlinks=False):
                    path = Path(entry.path)
                    if path == cache or entry.name in UV_SCAN_PRUNE:
                        continue
                    pending.append(path)
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
