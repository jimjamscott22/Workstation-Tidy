from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import TidyError


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.file = path / "history.json"

    def read(self) -> list[dict]:
        if not self.file.exists():
            return []
        if self.file.is_symlink() or self.path.resolve() != self.path:
            raise TidyError("History paths must not be symlinked")
        try:
            if self.file.stat().st_size > 5 * 1024 * 1024:
                raise TidyError("History file exceeds the supported size")
            data = json.loads(self.file.read_text())
            if not isinstance(data, list) or not all(isinstance(v, dict) for v in data):
                raise TidyError("Invalid history format")
            return data
        except (OSError, ValueError) as exc:
            raise TidyError(f"Cannot read cleanup history: {exc}") from exc

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.resolve() != self.path or self.path.stat().st_uid != os.getuid():
            raise TidyError("State directory must be canonical and owned by the current user")
        fd = os.open(self.path / "cleanup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TidyError("Another cleanup is already running") from exc
            self.read()  # Do not start deletion when history cannot be preserved.
            yield
        finally:
            os.close(fd)

    def save(self, run: dict) -> None:
        records = self.read()
        records = [r for r in records if r.get("id") != run["id"]]
        records.append(run)
        data = json.dumps(records[-50:], indent=2, ensure_ascii=True) + "\n"
        fd, name = tempfile.mkstemp(prefix="history-", suffix=".tmp", dir=self.path)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.file)
            directory_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)
