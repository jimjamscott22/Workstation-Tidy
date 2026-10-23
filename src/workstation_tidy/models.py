from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Kind = Literal["npm", "uv", "pip", "cargo", "thumbnails", "imager", "review"]
Outcome = Literal["cleaned", "partial", "failed", "skipped", "interrupted"]


@dataclass(frozen=True)
class Target:
    id: str
    kind: Kind
    path: Path
    category: str
    consequence: str


@dataclass
class Measurement:
    allocated_bytes: int = 0
    logical_bytes: int = 0
    files: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    target: Target
    size: Measurement
    identity: tuple[int, int] | None
    eligible: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["target"]["path"] = str(self.target.path)
        return value


@dataclass
class Result:
    target_id: str
    path: str
    outcome: Outcome
    reason: str
    before_bytes: int
    after_bytes: int | None


class TidyError(Exception):
    """An actionable operational or validation failure."""
