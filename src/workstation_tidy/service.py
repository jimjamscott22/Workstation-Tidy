from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from .config import Profile
from .handlers import Handler
from .models import Candidate, Outcome, Result, TidyError
from .safety import ProcessView, process_view
from .scanner import inspect
from .storage import Store


def filesystems(candidates: list[Candidate]) -> dict[str, dict[str, int | str]]:
    result: dict[str, dict[str, int | str]] = {}
    for candidate in candidates:
        path = candidate.target.path
        while not path.exists() and path != path.parent:
            path = path.parent
        info = path.stat()
        key = str(info.st_dev)
        if key not in result:
            fs = os.statvfs(path)
            result[key] = {"path": str(path), "available_bytes": fs.f_bavail * fs.f_frsize}
    return result


def clean(
    candidates: list[Candidate],
    profile: Profile,
    store: Store,
    handler: Handler | None = None,
    processes: Callable[[], ProcessView] = process_view,
) -> dict:
    handler = handler or Handler(profile)
    run: dict = {
        "id": str(uuid.uuid4()),
        "started_at": datetime.now(UTC).isoformat(),
        "profile": profile.name,
        "status": "running",
        "results": [],
        "filesystems": {},
    }
    with store.lock():
        before = filesystems(candidates)
        store.save(run)
        interrupted = False
        for candidate in candidates:
            if interrupted:
                result = Result(
                    candidate.target.id,
                    str(candidate.target.path),
                    "skipped",
                    "Run interrupted",
                    candidate.size.allocated_bytes,
                    None,
                )
            else:
                result = clean_one(candidate, handler, processes)
                interrupted = result.outcome == "interrupted"
            run["results"].append(asdict(result))
            run["status"] = "interrupted" if interrupted else "running"
            store.save(run)
        after = filesystems(candidates)
        for device, fs in before.items():
            end = after.get(device)
            run["filesystems"][device] = {
                "path": fs["path"],
                "before_available_bytes": fs["available_bytes"],
                "after_available_bytes": end["available_bytes"] if end else None,
                "available_change_bytes": int(end["available_bytes"]) - int(fs["available_bytes"])
                if end
                else None,
            }
        run["status"] = "interrupted" if interrupted else "completed"
        run["finished_at"] = datetime.now(UTC).isoformat()
        store.save(run)
    return run


def clean_one(
    candidate: Candidate, handler: Handler, processes: Callable[[], ProcessView]
) -> Result:
    target = candidate.target
    before = candidate.size.allocated_bytes
    try:
        command = handler.preflight(candidate, processes())
        fresh = inspect(target)
        if not fresh.eligible:
            raise TidyError(fresh.reason)
        before = fresh.size.allocated_bytes
    except KeyboardInterrupt:
        return Result(
            target.id,
            str(target.path),
            "interrupted",
            "Interrupted during preflight; cleanup not started",
            before,
            None,
        )
    except (TidyError, OSError) as exc:
        return Result(target.id, str(target.path), "skipped", str(exc), before, None)
    # The filesystem may have grown since the selection; measure again rather
    # than reporting the scan's old size as the execution baseline.
    code = None
    reason = ""
    interrupted = False
    try:
        code = handler.execute(candidate, command)
    except KeyboardInterrupt:
        interrupted = True
        reason = "Interrupted; completed deletions cannot be undone"
    except (OSError, TidyError) as exc:
        reason = f"Cleanup stopped: {exc}"
    try:
        if target.path.exists():
            remaining = inspect(target)
            after = remaining.size.allocated_bytes if not remaining.size.errors else None
            payload = handler.has_payload(target) if after is not None else True
        else:
            after, payload = 0, False
    except KeyboardInterrupt:
        return Result(
            target.id,
            str(target.path),
            "interrupted",
            "Interrupted during verification; cleanup may already have completed",
            before,
            None,
        )
    except OSError as exc:
        return Result(
            target.id,
            str(target.path),
            "failed",
            f"Cleanup outcome could not be measured: {exc}",
            before,
            None,
        )
    outcome: Outcome
    if interrupted:
        outcome = "interrupted"
    elif code == 0 and after is not None and not payload:
        outcome = "cleaned"
        reason = (
            "Verified no supported payload remains; cache metadata/empty directories may remain"
        )
    elif after is not None and after < before:
        outcome = "partial"
        reason = (
            reason
            or f"Command exited {code}; some payload remains or the command did not complete successfully"
        )
    else:
        outcome = "failed"
        reason = reason or f"Command exited {code}; cleanup could not be verified"
    return Result(target.id, str(target.path), outcome, reason, before, after)
