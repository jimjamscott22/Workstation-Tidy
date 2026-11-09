"""Optional Textual interface. Importing this package requires the 'tui' extra."""

from __future__ import annotations

from pathlib import Path

from ..config import Profile
from .app import TidyApp


def run(profile: Profile, *, dry_run: bool = False, config_path: Path | None = None) -> int:
    app = TidyApp(profile, dry_run=dry_run, config_path=config_path)
    result = app.run()
    if result is not None:
        return result
    return app.return_code or app.result_code


__all__ = ["TidyApp", "run"]
