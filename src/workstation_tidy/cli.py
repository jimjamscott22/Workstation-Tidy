from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .common import (
    check_environment,
    collect,
    filter_candidates,
    human,
    preflight_selected,
    safe,
)
from .config import Profile, config_path, load_profile, state_path
from .handlers import Handler
from .models import Candidate, TidyError
from .safety import process_view
from .service import clean, filesystems
from .storage import Store


def size_argument(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|K|M|G|T|KiB|MiB|GiB|TiB)?", value, re.IGNORECASE)
    if not match:
        raise argparse.ArgumentTypeError("Use bytes or a size such as 500MiB or 2G")
    suffix = (match[2] or "B").upper()[0]
    return int(float(match[1]) * 1024 ** "BKMGT".index(suffix))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Inspect and explicitly select user-level workstation cleanup."
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command")
    for name, help_text in (
        ("scan", "Read-only ranked disk usage report"),
        ("clean", "Interactively select, preview, confirm and clean"),
        ("show", "Explain a target by ID or absolute path"),
        ("doctor", "Check tools, configuration and process visibility"),
        ("history", "Show previous cleanup outcomes"),
        ("config", "Show effective locations and exclusions"),
        ("tui", "Interactive terminal interface (needs the optional 'tui' extra)"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument(
            "--config",
            type=Path,
            default=config_path(),
            help="Profile TOML file (defaults apply if missing)",
        )
        sub.add_argument("--profile", default="usual")
        if name not in {"clean", "tui"}:
            sub.add_argument("--json", action="store_true", help="Emit a versioned JSON report")
        if name in {"scan", "clean"}:
            sub.add_argument("--min-size", type=size_argument, default=0)
            sub.add_argument("--category", choices=("packages", "build", "desktop", "review"))
        if name in {"clean", "tui"}:
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview without deleting; non-TTY previews all supported candidates",
            )
        if name == "show":
            sub.add_argument("target", help="Stable target ID or exact absolute path from scan")
    return root


def report(value: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"schema_version": 1, **value}, indent=2, ensure_ascii=True))
    else:
        for key, content in value.items():
            print(f"{safe(key)}: {safe(content)}")


def display(candidates: list[Candidate]) -> None:
    for index, candidate in enumerate(candidates, 1):
        target = candidate.target
        status = "candidate" if candidate.eligible else "review/blocked"
        print(
            f"{index:>3}. {human(candidate.size.allocated_bytes):>11}  {target.category:8}  {status:14} {safe(target.id)}"
        )
        print(f"     {safe(target.path)}")
        print(f"     {safe(candidate.reason)}")


def selection(text: str, count: int) -> list[int]:
    if not text.strip():
        return []
    parts = text.replace(",", " ").split()
    if not all(p.isdecimal() for p in parts):
        raise TidyError("Choose numbers separated by spaces or commas; no targets were selected")
    indices = sorted({int(p) - 1 for p in parts})
    if any(i < 0 or i >= count for i in indices):
        raise TidyError("Selection contains an out-of-range number")
    return indices


def interactive_clean(candidates: list[Candidate], profile: Profile, dry_run: bool) -> int:
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive and not dry_run:
        raise TidyError(
            "Cleanup requires an interactive terminal. Use scan --json or clean --dry-run for reporting."
        )
    display(candidates)
    if interactive:
        indices = selection(
            input("Select target numbers (none preselected; Enter cancels): "), len(candidates)
        )
    else:
        indices = [i for i, c in enumerate(candidates) if c.eligible]
    selected = [candidates[i] for i in indices]
    if not selected:
        print("No targets selected; nothing changed.")
        return 0
    eligible = []
    view = process_view()
    if view.opaque:
        print("Not inspected (session daemons): " + safe(", ".join(view.opaque)))
    for candidate, operation, skip in preflight_selected(selected, profile, view):
        print(f"\n{safe(candidate.target.id)}: {safe(candidate.target.path)}")
        print(safe(candidate.target.consequence))
        if operation is not None:
            print("Operation: " + safe(operation))
            eligible.append(candidate)
        else:
            print("SKIP: " + safe(skip))
    if dry_run:
        print("\nDry run complete. No cleanup or history writes performed.")
        return 0
    if not eligible:
        print("No selected targets passed safety checks; nothing changed.")
        return 1
    print(
        f"\n{len(eligible)} target(s); estimated allocated footprint {human(sum(c.size.allocated_bytes for c in eligible))}."
    )
    print("Permanent cleanup: no undo. Future installs/builds may download or rebuild files.")
    if input("Type CLEAN to clean only the eligible targets above: ").strip() != "CLEAN":
        print("Cancelled; nothing changed.")
        return 0
    run = clean(eligible, profile, Store(state_path()))
    for result in run["results"]:
        print(f"{result['outcome'].upper()}: {safe(result['target_id'])}: {safe(result['reason'])}")
    for fs in run["filesystems"].values():
        delta = fs["available_change_bytes"]
        print(
            f"Filesystem free-space change: {human(delta) if delta is not None else 'unverified'}; available now: {human(fs['after_available_bytes']) if fs['after_available_bytes'] is not None else 'unverified'}"
        )
    print("Free-space changes may include concurrent activity and differ from cache estimates.")
    if run["status"] == "interrupted":
        return 130
    return 0 if all(r["outcome"] == "cleaned" for r in run["results"]) else 1


def launch_tui(profile: Profile, dry_run: bool, config: Path) -> int:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise TidyError("The TUI requires an interactive terminal. Use scan or clean --dry-run.")
    try:
        from .tui import run
    except ImportError:
        print(
            "The TUI needs the optional 'tui' extra. Install it with "
            "`uv tool install '.[tui]'` or, in this project, `uv sync --extra tui`.",
            file=sys.stderr,
        )
        return 1
    return run(profile, dry_run=dry_run, config_path=config)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 0
    try:
        check_environment()
        profile = load_profile(args.profile, args.config)
        if args.command == "tui":
            return launch_tui(profile, args.dry_run, args.config)
        if args.command == "config":
            report(
                {
                    "config_path": str(args.config),
                    "using_defaults": not args.config.exists(),
                    "profile": profile.name,
                    "project_roots": [str(p) for p in profile.project_roots],
                    "discover_cargo": profile.discover_cargo,
                    "exclusions": [str(p) for p in profile.exclude],
                    "targets": [
                        {"id": t.id, "kind": t.kind, "path": str(t.path)} for t in profile.targets
                    ],
                },
                args.json,
            )
            return 0
        if args.command == "history":
            records = Store(state_path()).read()
            if args.json:
                report({"runs": records}, True)
            elif not records:
                print("No cleanup history yet.")
            else:
                for run in records:
                    print(
                        safe(
                            f"{run.get('started_at')}  {run.get('status')}  profile={run.get('profile')}"
                        )
                    )
                    for result in run.get("results", []):
                        print(safe(f"  {result['outcome']}: {result['path']} — {result['reason']}"))
            return 0
        if args.command == "doctor":
            view = process_view()
            candidates, errors = collect(profile)
            tools = {name: shutil.which(name) for name in ("npm", "uv", "pip3", "cargo", "git")}
            report(
                {
                    "tools": tools,
                    "process_visibility": "verified" if not view.errors else "insufficient",
                    "process_errors": view.errors,
                    "process_not_inspected": view.opaque,
                    "discovery_errors": errors,
                    "targets": [
                        {"id": c.target.id, "eligible": c.eligible, "reason": c.reason}
                        for c in candidates
                    ],
                    "note": "Candidate status is preliminary; active-use and dependency checks run again before cleanup.",
                },
                args.json,
            )
            return 1 if view.errors or errors else 0
        candidates, errors = collect(profile)
        if args.command == "show":
            matched = [
                c
                for c in candidates
                if c.target.id == args.target or str(c.target.path) == args.target
            ]
            if not matched:
                raise TidyError("Target not found; use scan to list IDs and paths")
            candidate = matched[0]
            value = candidate.to_dict()
            try:
                value["operation"] = (
                    Handler(profile).preview(candidate.target) if candidate.eligible else None
                )
            except TidyError:
                value["operation"] = None
            report(value, args.json)
            return 0
        candidates = filter_candidates(candidates, args.min_size, args.category)
        if args.command == "scan":
            if args.json:
                report(
                    {
                        "profile": profile.name,
                        "candidates": [c.to_dict() for c in candidates],
                        "errors": errors,
                        "filesystems": filesystems(candidates),
                        "note": "Allocated estimates, not guaranteed reclaimable space. Candidates require cleanup preflight.",
                    },
                    True,
                )
            else:
                display(candidates)
                print(
                    "Allocated estimates; candidates require active-use and dependency checks before cleaning."
                )
                for error in errors:
                    print("Warning: " + safe(error))
            return 1 if errors else 0
        if errors:
            for error in errors:
                print("Warning: " + safe(error), file=sys.stderr)
        return interactive_clean(candidates, profile, args.dry_run)
    except (TidyError, OSError, ValueError) as exc:
        if getattr(args, "json", False):
            report({"error": str(exc)}, True)
        else:
            print("Error: " + safe(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Any completed cleanup is retained in history.", file=sys.stderr)
        return 130
