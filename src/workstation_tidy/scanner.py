from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .config import CONSEQUENCES, Profile
from .models import Candidate, Measurement, Target


def path_identity(path: Path) -> tuple[int, int]:
    """Reject symlinked ancestors as well as a symlinked target."""
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise OSError("Path is not canonical or contains a symlink")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("Target is not a directory")
    if info.st_uid != os.getuid():
        raise OSError("Target is not owned by the current user")
    return info.st_dev, info.st_ino


def measure(
    path: Path, seen: set[tuple[int, int]] | None = None, *, allow_internal_links: bool = False
) -> Measurement:
    """Count allocated blocks without following links or crossing devices.

    Directories are opened relative to held directory descriptors to avoid
    following a directory replaced with a symlink during traversal.
    """
    result = Measurement()
    seen = seen if seen is not None else set()
    try:
        root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        result.errors.append(f"Cannot inspect {path}: {exc.strerror or exc}")
        return result

    def walk(fd: int, device: int, relative: Path = Path()) -> None:
        info = os.fstat(fd)
        ident = info.st_dev, info.st_ino
        if ident in seen:
            return
        seen.add(ident)
        result.allocated_bytes += info.st_blocks * 512
        try:
            names = os.listdir(fd)
        except OSError as exc:
            result.errors.append(f"Directory unreadable: {exc.strerror}")
            return
        for name in names:
            try:
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                key = entry.st_dev, entry.st_ino
                if entry.st_uid != os.getuid():
                    result.errors.append("Entry is not owned by the current user")
                elif entry.st_dev != device:
                    result.errors.append("Nested filesystem excluded")
                elif stat.S_ISLNK(entry.st_mode):
                    if allow_internal_links:
                        destination = (path / relative / os.readlink(name, dir_fd=fd)).resolve()
                        if not destination.is_relative_to(path):
                            result.errors.append("Cache symlink escapes the selected target")
                    else:
                        result.errors.append("Symlink present; not followed")
                elif stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child, device, relative / name)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(entry.st_mode):
                    if key not in seen:
                        seen.add(key)
                        result.allocated_bytes += entry.st_blocks * 512
                        result.logical_bytes += entry.st_size
                        result.files += 1
                else:
                    result.errors.append("Special file present; cleanup requires review")
            except (OSError, RuntimeError) as exc:
                result.errors.append(f"Entry changed or inaccessible: {exc}")

    try:
        walk(root, os.fstat(root).st_dev)
    finally:
        os.close(root)
    result.errors = sorted(set(result.errors))
    return result


def inspect(target: Target, seen: set[tuple[int, int]] | None = None) -> Candidate:
    try:
        identity = path_identity(target.path)
    except FileNotFoundError:
        return Candidate(target, Measurement(), None, False, "Missing; nothing to clean")
    except (OSError, RuntimeError) as exc:
        return Candidate(target, Measurement(errors=[str(exc)]), None, False, str(exc))
    size = measure(target.path, seen, allow_internal_links=target.kind == "uv")
    if target.kind == "review":
        return Candidate(target, size, identity, False, target.consequence)
    if size.errors:
        return Candidate(target, size, identity, False, "; ".join(size.errors))
    return Candidate(
        target, size, identity, True, "Candidate; safety preflight required before cleaning"
    )


def discover(
    roots: tuple[Path, ...], exclusions: tuple[Path, ...]
) -> tuple[list[Target], list[str]]:
    targets: list[Target] = []
    errors: list[str] = []
    prune = {".git", "node_modules", ".venv", "venv", "target", "__pycache__"}
    for root in roots:
        try:
            path_identity(root)
        except (OSError, RuntimeError) as exc:
            errors.append(f"Cannot discover below {root}: {exc}")
            continue
        device = root.stat().st_dev

        def error(exc: OSError) -> None:
            errors.append(f"Discovery incomplete: {exc}")

        for current, dirs, files in os.walk(root, followlinks=False, onerror=error):
            path = Path(current)
            if any(path == e or e in path.parents for e in exclusions):
                dirs[:] = []
                continue
            if len(path.relative_to(root).parts) >= 12:
                if dirs:
                    errors.append(f"Discovery depth limit reached: {path}")
                dirs[:] = []
            kept = []
            for name in dirs:
                p = path / name
                try:
                    if name not in prune and not p.is_symlink() and p.stat().st_dev == device:
                        kept.append(name)
                except OSError as exc:
                    error(exc)
            dirs[:] = kept
            output = path / "target"
            if (
                "Cargo.toml" in files
                and output.exists()
                and not any(output == e or e in output.parents for e in exclusions)
            ):
                ident = hashlib.sha256(str(output).encode()).hexdigest()[:12]
                targets.append(
                    Target(f"discovered-{ident}", "cargo", output, "build", CONSEQUENCES["cargo"])
                )
    return targets, errors


def scan(profile: Profile) -> tuple[list[Candidate], list[str]]:
    targets = list(profile.targets)
    errors: list[str] = []
    if profile.discover_cargo:
        extra, errors = discover(profile.project_roots, profile.exclude)
        known = {t.path for t in targets}
        for t in extra:
            if t.path not in known:
                if any(p in t.path.parents or t.path in p.parents for p in known):
                    errors.append(f"Overlapping discovery excluded: {t.path}")
                else:
                    targets.append(t)
                    known.add(t.path)
    # Stable ordering makes hardlink attribution deterministic across reports.
    seen: set[tuple[int, int]] = set()
    candidates = []
    for target in sorted(targets, key=lambda t: str(t.path)):
        candidate = inspect(target, seen)
        if any(target.path in p.parents for p in profile.exclude):
            candidate.eligible = False
            candidate.reason = "Contains an excluded path; whole-target cleanup is disabled"
        candidates.append(candidate)
    return sorted(candidates, key=lambda c: (-c.size.allocated_bytes, c.target.id)), errors
