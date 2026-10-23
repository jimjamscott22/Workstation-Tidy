from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .models import Target, TidyError

CARGO_PATHS = (
    "Code-Vault/target",
    "DogWalk-Visualizer/src-tauri/target",
    "Config-Forge/src-tauri/target",
    "Villiage-Sims/src-tauri/target",
    "DogWalk-Visualizer/.worktrees/single-dog-time-analytics/src-tauri/target",
    "RSync-Buddy/src-tauri/target",
    "Rusty-Vault/target",
    "Linux-File-View/src-tauri/target",
)
CACHE_PATHS = {
    "npm": ".npm/_cacache",
    "uv": ".cache/uv",
    "pip": ".cache/pip",
    "thumbnails": ".cache/thumbnails",
    "imager": ".cache/Raspberry Pi/Imager",
}
CONSEQUENCES = {
    "npm": "Downloaded packages will be fetched again; installed dependencies and npx are retained.",
    "uv": "Cached Python packages will be downloaded/rebuilt again; cache-dependent symlinks block cleanup.",
    "pip": "Downloaded Python packages and wheels will be fetched/built again.",
    "cargo": "Compiled applications, dependencies and installers are removed; rebuilding takes longer.",
    "thumbnails": "Image previews will be regenerated.",
    "imager": "The last downloaded OS image must be downloaded again.",
}
REVIEW_PATHS = (
    (
        "jetbrains-cache",
        ".cache/JetBrains",
        "Includes Local History; manage caches through the IDE.",
    ),
    ("jetbrains-apps", ".local/share/JetBrains", "Installed IDEs, plugins and application data."),
    (
        "cursor",
        ".config/Cursor",
        "Settings, recovery state and agent runtimes; no automatic deletion.",
    ),
    (
        "chrome",
        ".cache/google-chrome",
        "Clear cache through the browser; preserve cookies and site data.",
    ),
    (
        "lmstudio",
        ".lmstudio",
        "Downloaded models, runtimes and conversations; use application management.",
    ),
    (
        "playwright",
        ".cache/ms-playwright",
        "Browser executables may be required by current projects.",
    ),
    ("puppeteer", ".cache/puppeteer", "Browser executables may be required by current projects."),
)


@dataclass(frozen=True)
class Profile:
    name: str
    home: Path
    targets: tuple[Target, ...]
    project_roots: tuple[Path, ...]
    exclude: tuple[Path, ...]
    discover_cargo: bool


def config_path() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "workstation-tidy/config.toml"
    )


def state_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "workstation-tidy"


def expand(value: str, home: Path) -> Path:
    if value == "~" or value.startswith("~/"):
        value = str(home) + value[1:]
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise TidyError(f"Use absolute or ~/ paths without '..': {value}")
    return path


def string_list(data: dict, key: str, default: list[str]) -> list[str]:
    values = data.get(key, default)
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise TidyError(f"{key} must be an array of paths")
    return values


def load_profile(
    name: str = "usual", path: Path | None = None, home: Path | None = None
) -> Profile:
    home = home or Path.home()
    path = path or config_path()
    try:
        document = (
            tomllib.loads(path.read_text())
            if path.exists()
            else {"version": 1, "profiles": {"usual": {}}}
        )
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TidyError(f"Cannot read configuration: {exc}") from exc
    if set(document) - {"version", "profiles"} or document.get("version") != 1:
        raise TidyError("Configuration requires version = 1 and a profiles table")
    profiles = document.get("profiles", {})
    if (
        not isinstance(profiles, dict)
        or name not in profiles
        or not isinstance(profiles[name], dict)
    ):
        raise TidyError(f"Unknown profile: {name}")
    data = profiles[name]
    if set(data) - {
        "project_roots",
        "cargo_targets",
        "exclude",
        "discover_cargo",
        "caches",
        "review",
    }:
        raise TidyError("Unknown profile keys; arbitrary commands are not supported")
    if not isinstance(data.get("discover_cargo", False), bool) or not isinstance(
        data.get("review", True), bool
    ):
        raise TidyError("discover_cargo and review must be booleans")
    roots = tuple(
        expand(p, home) for p in string_list(data, "project_roots", ["~/Desktop/GitHub-Repos"])
    )
    if any(not p.is_relative_to(home) or p == home for p in roots):
        raise TidyError("Project discovery roots must be below the current user's home")
    exclude = tuple(expand(p, home) for p in string_list(data, "exclude", []))
    cargo = string_list(data, "cargo_targets", [f"~/Desktop/GitHub-Repos/{p}" for p in CARGO_PATHS])
    caches = data.get("caches", CACHE_PATHS)
    if not isinstance(caches, dict) or set(caches) - set(CACHE_PATHS):
        raise TidyError("caches must map supported cache names to paths or false")
    targets: list[Target] = []
    for key, value in caches.items():
        if value is False:
            continue
        if not isinstance(value, str):
            raise TidyError(f"Invalid cache path for {key}")
        # Default paths are home-relative; user supplied paths must be explicit.
        cache_path = home / value if caches is CACHE_PATHS else expand(value, home)
        targets.append(
            Target(
                key,
                key,
                cache_path,
                "desktop" if key in {"thumbnails", "imager"} else "packages",
                CONSEQUENCES[key],
            )
        )
    for i, cargo_path in enumerate(cargo, 1):
        targets.append(
            Target(f"cargo-{i}", "cargo", expand(cargo_path, home), "build", CONSEQUENCES["cargo"])
        )
    if data.get("review", True):
        for ident, rel, reason in REVIEW_PATHS:
            targets.append(Target(ident, "review", home / rel, "review", reason))
    targets = [t for t in targets if not any(t.path == e or e in t.path.parents for e in exclude)]
    seen: list[Path] = []
    for target in targets:
        if not target.path.is_relative_to(home) or target.path == home:
            raise TidyError(f"Targets must be below the current user's home: {target.path}")
        if target.path in seen:
            raise TidyError(f"Duplicate target: {target.path}")
        if any(p in target.path.parents or target.path in p.parents for p in seen):
            raise TidyError(f"Overlapping targets: {target.path}")
        seen.append(target.path)
    return Profile(name, home, tuple(targets), roots, exclude, data.get("discover_cargo", False))
