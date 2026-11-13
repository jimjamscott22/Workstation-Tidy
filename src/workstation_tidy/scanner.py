from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

from .config import (
    AMBIGUOUS_CONSEQUENCE,
    AMBIGUOUS_DIRS,
    AMBIGUOUS_MARKERS,
    CONSEQUENCES,
    DESCENT_PRUNE,
    FRONTEND_DIRS,
    LOCKFILES,
    PYTHON_MARKERS,
    TOOL_CACHE_DIRS,
    Profile,
)
from .models import Candidate, Kind, Measurement, Target


def is_reparse(path: Path) -> bool:
    """True for symlinks and Windows junctions.

    A missing path is not a link. Other stat failures are treated as links so
    discovery does not descend into an entry it cannot classify.
    """
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        if checker is None:
            return False
        return bool(checker())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def path_identity(path: Path) -> tuple[int, int]:
    """Reject symlinked ancestors as well as a symlinked target."""
    if not path.is_absolute() or is_reparse(path):
        raise OSError("Path is not canonical or contains a symlink")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise OSError("Path is not canonical or contains a symlink") from exc
    if sys.platform.startswith("linux"):
        if resolved != path:
            raise OSError("Path is not canonical or contains a symlink")
    elif os.path.normcase(str(resolved)) != os.path.normcase(str(path)):
        raise OSError("Path is not canonical or contains a symlink")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("Target is not a directory")
    # Windows file ownership is not a usable stand-in for the Linux uid check.
    # Reports there are identification only and cannot authorize deletion.
    if sys.platform.startswith("linux") and info.st_uid != os.getuid():
        raise OSError("Target is not owned by the current user")
    return info.st_dev, info.st_ino


def _note_mtime(result: Measurement, mtime: float) -> None:
    if result.newest_mtime is None or mtime > result.newest_mtime:
        result.newest_mtime = mtime


def _finish(result: Measurement) -> Measurement:
    result.errors = sorted(set(result.errors))
    return result


def measure(
    path: Path,
    seen: set[tuple[int, int]] | None = None,
    *,
    allow_internal_links: bool = False,
    keep_links: bool = False,
) -> Measurement:
    """Count allocated blocks without following links or crossing devices.

    On Linux, directories are opened relative to held directory descriptors so a
    directory replaced with a symlink during traversal is not followed. Windows
    has no equivalent no-follow open, so identification uses scandir instead.
    """
    if not sys.platform.startswith("linux"):
        return _measure_scandir(
            path, seen, allow_internal_links=allow_internal_links, keep_links=keep_links
        )
    return _measure_descriptors(
        path, seen, allow_internal_links=allow_internal_links, keep_links=keep_links
    )


def _measure_descriptors(
    path: Path,
    seen: set[tuple[int, int]] | None,
    *,
    allow_internal_links: bool,
    keep_links: bool,
) -> Measurement:
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
        _note_mtime(result, info.st_mtime)
        try:
            names = os.listdir(fd)
        except OSError as exc:
            result.errors.append(f"Directory unreadable: {exc.strerror}")
            return
        for name in names:
            try:
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                key = entry.st_dev, entry.st_ino
                _note_mtime(result, entry.st_mtime)
                if entry.st_uid != os.getuid():
                    result.errors.append("Entry is not owned by the current user")
                elif entry.st_dev != device:
                    result.errors.append("Nested filesystem excluded")
                elif stat.S_ISLNK(entry.st_mode):
                    if keep_links:
                        continue
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
    return _finish(result)


def _allocated_bytes(info: os.stat_result) -> int:
    blocks = getattr(info, "st_blocks", 0) or 0
    if blocks:
        return blocks * 512
    # Windows stat results leave st_blocks at 0. Identification reports the
    # logical size so a folder still ranks by the space it occupies.
    if stat.S_ISREG(info.st_mode):
        return info.st_size
    return 0


def _measure_scandir(
    path: Path,
    seen: set[tuple[int, int]] | None,
    *,
    allow_internal_links: bool,
    keep_links: bool,
) -> Measurement:
    result = Measurement()
    seen = seen if seen is not None else set()
    try:
        if is_reparse(path):
            result.errors.append("Symlink present; not followed")
            return _finish(result)
        root_info = path.lstat()
    except OSError as exc:
        result.errors.append(f"Cannot inspect {path}: {exc.strerror or exc}")
        return result

    def walk(directory: Path, device: int) -> None:
        try:
            info = directory.lstat()
        except OSError as exc:
            result.errors.append(f"Entry changed or inaccessible: {exc}")
            return
        ident = info.st_dev, info.st_ino
        if ident in seen:
            return
        seen.add(ident)
        result.allocated_bytes += _allocated_bytes(info)
        _note_mtime(result, info.st_mtime)
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            result.errors.append(f"Directory unreadable: {exc.strerror or exc}")
            return
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
                key = st.st_dev, st.st_ino
                _note_mtime(result, st.st_mtime)
                linked = entry.is_symlink() or is_reparse(Path(entry.path))
                if linked:
                    if keep_links:
                        continue
                    if allow_internal_links:
                        raw = Path(os.readlink(entry.path))
                        destination = raw if raw.is_absolute() else directory / raw
                        if not destination.resolve().is_relative_to(path):
                            result.errors.append("Cache symlink escapes the selected target")
                    else:
                        result.errors.append("Symlink present; not followed")
                    continue
                if st.st_dev != device:
                    result.errors.append("Nested filesystem excluded")
                elif stat.S_ISDIR(st.st_mode):
                    walk(Path(entry.path), device)
                elif stat.S_ISREG(st.st_mode):
                    if key not in seen:
                        seen.add(key)
                        result.allocated_bytes += _allocated_bytes(st)
                        result.logical_bytes += st.st_size
                        result.files += 1
                else:
                    result.errors.append("Special file present; cleanup requires review")
            except (OSError, RuntimeError) as exc:
                result.errors.append(f"Entry changed or inaccessible: {exc}")

    walk(path, root_info.st_dev)
    return _finish(result)


def _keeps_links(target: Target) -> bool:
    # Project artifacts routinely contain symlinks (.bin shims, venv interpreters).
    # The link itself is what gets removed; the destination stays put.
    return target.kind in {"node_modules", "venv", "tool_cache", "frontend_cache"} or bool(
        target.detail
    )


def inspect(target: Target, seen: set[tuple[int, int]] | None = None) -> Candidate:
    try:
        identity = path_identity(target.path)
    except FileNotFoundError:
        return Candidate(target, Measurement(), None, False, "Missing; nothing to clean")
    except (OSError, RuntimeError) as exc:
        return Candidate(target, Measurement(errors=[str(exc)]), None, False, str(exc))
    size = measure(
        target.path,
        seen,
        allow_internal_links=target.kind == "uv",
        keep_links=_keeps_links(target),
    )
    if target.kind == "review":
        return Candidate(target, size, identity, False, target.consequence)
    if size.errors:
        return Candidate(target, size, identity, False, "; ".join(size.errors))
    return Candidate(
        target, size, identity, True, "Candidate; safety preflight required before cleaning"
    )


def _artifact_id(path: Path) -> str:
    return "artifact-" + hashlib.sha256(str(path).encode()).hexdigest()[:12]


def _detail(project: Path, marker: str) -> str:
    return f"project={project}; marker={marker}"


def _excluded(path: Path, exclusions: tuple[Path, ...]) -> bool:
    return any(path == excluded or excluded in path.parents for excluded in exclusions)


def _marker(names: set[str], folder: Path, choices: tuple[str, ...]) -> str | None:
    for name in choices:
        if name in names and is_regular_file(folder / name):
            return name
    return None


def _classify_artifact(
    project: Path, name: str, names: set[str], exclusions: tuple[Path, ...]
) -> Target | None:
    folder = project / name
    if _excluded(folder, exclusions):
        return None
    ident = _artifact_id(folder)
    kind: Kind
    consequence: str
    category: str
    marker: str | None
    if name == "node_modules" and _marker(names, project, ("package.json",)) == "package.json":
        lock = _marker(names, project, LOCKFILES)
        marker = lock or "package.json (no lockfile)"
        kind, category, consequence = "node_modules", "build", CONSEQUENCES["node_modules"]
    elif name in {".venv", "venv"} and is_regular_file(folder / "pyvenv.cfg"):
        marker = "pyvenv.cfg"
        kind, category, consequence = "venv", "build", CONSEQUENCES["venv"]
    elif name in TOOL_CACHE_DIRS and (marker := _marker(names, project, PYTHON_MARKERS)):
        kind, category, consequence = "tool_cache", "build", CONSEQUENCES["tool_cache"]
    elif name in FRONTEND_DIRS and _marker(names, project, ("package.json",)) == "package.json":
        marker = "package.json"
        kind, category, consequence = "frontend_cache", "build", CONSEQUENCES["frontend_cache"]
    elif name in AMBIGUOUS_DIRS and (marker := _marker(names, project, AMBIGUOUS_MARKERS)):
        kind, category, consequence = "review", "review", AMBIGUOUS_CONSEQUENCE
    else:
        return None
    if marker is None:
        return None
    return Target(ident, kind, folder, category, consequence, _detail(project, marker))


def _walk_projects(roots: tuple[Path, ...], exclusions: tuple[Path, ...]) -> tuple[list, list[str]]:
    """Visit project directories without following links or entering artifact trees.

    The returned rows are ``(directory, filenames, child directory names)``.
    """
    found: list[tuple[Path, list[str], list[str]]] = []
    errors: list[str] = []
    for root in roots:
        try:
            path_identity(root)
        except (OSError, RuntimeError) as exc:
            errors.append(f"Cannot discover below {root}: {exc}")
            continue
        device = root.stat().st_dev

        def onerror(exc: OSError) -> None:
            errors.append(f"Discovery incomplete: {exc}")

        for current, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
            path = Path(current)
            child_names = list(dirs)
            if _excluded(path, exclusions):
                dirs[:] = []
                continue
            if len(path.relative_to(root).parts) >= 12:
                if child_names:
                    errors.append(f"Discovery depth limit reached: {path}")
                dirs[:] = []
            else:
                kept: list[str] = []
                for name in child_names:
                    child = path / name
                    try:
                        if (
                            name not in DESCENT_PRUNE
                            and not is_reparse(child)
                            and not child.is_symlink()
                            and child.stat().st_dev == device
                        ):
                            kept.append(name)
                    except OSError as exc:
                        onerror(exc)
                dirs[:] = kept
            found.append((path, files, child_names))
    return found, errors


def discover(
    roots: tuple[Path, ...], exclusions: tuple[Path, ...]
) -> tuple[list[Target], list[str]]:
    targets: list[Target] = []
    rows, errors = _walk_projects(roots, exclusions)
    for path, files, _child_names in rows:
        output = path / "target"
        if "Cargo.toml" in files and output.exists() and not _excluded(output, exclusions):
            ident = hashlib.sha256(str(output).encode()).hexdigest()[:12]
            targets.append(
                Target(f"discovered-{ident}", "cargo", output, "build", CONSEQUENCES["cargo"])
            )
    return targets, errors


def discover_artifacts(
    roots: tuple[Path, ...],
    exclusions: tuple[Path, ...],
    *,
    missing_ok: bool,
) -> tuple[list[Target], list[str]]:
    present: list[Path] = []
    errors: list[str] = []
    for root in roots:
        if root.exists():
            present.append(root)
        elif not missing_ok:
            errors.append(f"Cannot discover below {root}: project root is missing")
    targets: list[Target] = []
    rows, walk_errors = _walk_projects(tuple(present), exclusions)
    errors.extend(walk_errors)
    for path, files, child_names in rows:
        names = set(files)
        for name in child_names:
            target = _classify_artifact(path, name, names, exclusions)
            if target is not None:
                targets.append(target)
    return targets, errors


def _add_discovered(targets: list[Target], extra: list[Target], errors: list[str]) -> None:
    known = {target.path for target in targets}
    for found in extra:
        if found.path in known:
            continue
        if any(
            known_path in found.path.parents or found.path in known_path.parents
            for known_path in known
        ):
            errors.append(f"Overlapping discovery excluded: {found.path}")
            continue
        targets.append(found)
        known.add(found.path)


def scan(profile: Profile) -> tuple[list[Candidate], list[str]]:
    targets = list(profile.targets)
    errors: list[str] = []
    if profile.discover_cargo:
        extra, cargo_errors = discover(profile.project_roots, profile.exclude)
        errors.extend(cargo_errors)
        _add_discovered(targets, extra, errors)
    if profile.discover_artifacts:
        extra, artifact_errors = discover_artifacts(
            profile.project_roots,
            profile.exclude,
            missing_ok=not profile.project_roots_explicit,
        )
        errors.extend(artifact_errors)
        _add_discovered(targets, extra, errors)
    # Stable ordering makes hardlink attribution deterministic across reports.
    seen: set[tuple[int, int]] = set()
    candidates = []
    for target in sorted(targets, key=lambda item: str(item.path)):
        candidate = inspect(target, seen)
        if any(target.path in excluded.parents for excluded in profile.exclude):
            candidate.eligible = False
            candidate.reason = "Contains an excluded path; whole-target cleanup is disabled"
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: (-item.size.allocated_bytes, item.target.id)), errors
