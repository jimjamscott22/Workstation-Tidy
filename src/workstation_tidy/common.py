"""Helpers shared by the CLI and the TUI."""

from __future__ import annotations

import os
import shlex
import sys
import time
from datetime import datetime

from .config import Profile
from .handlers import Handler
from .models import Candidate, TidyError
from .safety import ProcessView, process_view
from .scanner import scan

READ_ONLY_COMMANDS = frozenset({"scan", "show", "doctor", "history", "config"})


def human(value: int) -> str:
    size = float(abs(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{'-' if value < 0 else ''}{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def safe(value: object) -> str:
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in str(value))


def check_environment(command: str | None = None) -> None:
    """Allow identification commands on Windows. Cleanup stays Linux-only.

    os.geteuid() does not exist on Windows, so the platform is checked first.
    """
    if not sys.platform.startswith("linux"):
        if command in READ_ONLY_COMMANDS:
            return
        raise TidyError(
            "Cleanup requires Linux, where process checks can see open files. "
            "On Windows, use scan or show to identify the same project artifacts."
        )
    if os.geteuid() == 0:
        raise TidyError("Run as your normal user, without sudo; root execution is not supported")


def split_detail(detail: str) -> tuple[str, str]:
    project = ""
    marker = ""
    for part in detail.split("; "):
        if part.startswith("project="):
            project = part.removeprefix("project=")
        elif part.startswith("marker="):
            marker = part.removeprefix("marker=")
    return project, marker


def modified_text(mtime: float | None) -> str:
    if mtime is None:
        return "modified time unknown"
    when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    days = int((time.time() - mtime) // 86400)
    if days <= 0:
        return f"modified {when} (today)"
    unit = "day" if days == 1 else "days"
    return f"modified {when} ({days} {unit})"


def collect(profile: Profile) -> tuple[list[Candidate], list[str]]:
    candidates, errors = scan(profile)
    handler = Handler(profile)
    for candidate in candidates:
        if candidate.eligible:
            try:
                handler.check_layout(candidate.target)
                handler.preview(candidate.target)
            except (OSError, TidyError) as exc:
                candidate.eligible = False
                candidate.reason = str(exc)
    return candidates, errors


def filter_candidates(
    candidates: list[Candidate],
    min_size: int = 0,
    category: str | None = None,
    *,
    older_than: float | None = None,
) -> list[Candidate]:
    now = time.time()
    selected = []
    for candidate in candidates:
        if candidate.size.allocated_bytes < min_size:
            continue
        if category and candidate.target.category != category:
            continue
        if older_than is not None:
            modified = candidate.size.newest_mtime
            if modified is None or (now - modified) < older_than:
                continue
        selected.append(candidate)
    return selected


def preflight_selected(
    selected: list[Candidate], profile: Profile, view: ProcessView | None = None
) -> list[tuple[Candidate, str | None, str | None]]:
    """Return (candidate, operation, skip_reason) for each selected candidate."""
    handler = Handler(profile)
    view = view if view is not None else process_view()
    checked: list[tuple[Candidate, str | None, str | None]] = []
    for candidate in selected:
        try:
            command = handler.preflight(candidate, view)
            checked.append((candidate, shlex.join(str(part) for part in command), None))
        except (TidyError, OSError) as exc:
            checked.append((candidate, None, str(exc)))
    return checked
