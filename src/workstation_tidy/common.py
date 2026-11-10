"""Helpers shared by the CLI and the TUI."""

from __future__ import annotations

import os
import shlex
import sys

from .config import Profile
from .handlers import Handler
from .models import Candidate, TidyError
from .safety import ProcessView, process_view
from .scanner import scan


def human(value: int) -> str:
    size = float(abs(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{'-' if value < 0 else ''}{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def safe(value: object) -> str:
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in str(value))


def check_environment() -> None:
    # Check the platform first: os.geteuid() does not exist on Windows.
    if not sys.platform.startswith("linux"):
        raise TidyError("Workstation Tidy currently supports Linux only")
    if os.geteuid() == 0:
        raise TidyError("Run as your normal user, without sudo; root execution is not supported")


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
    candidates: list[Candidate], min_size: int = 0, category: str | None = None
) -> list[Candidate]:
    return [
        c
        for c in candidates
        if c.size.allocated_bytes >= min_size and (not category or c.target.category == category)
    ]


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
