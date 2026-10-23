# Workstation Tidy

A small, local Python CLI for Ubuntu developer and desktop cleanup:

**Scan → explain → select → preview → confirm → clean → verify.**

Python 3.11+ and Linux are required. The application has no third-party runtime dependencies. Cleanup tools (`npm`, `uv`, `pip3`, Cargo and Git) are optional: missing tools block their handlers while leaving reports available.

## Run

From this project:

```sh
uv sync
uv run workstation-tidy scan
uv run workstation-tidy scan --min-size 500MiB
uv run workstation-tidy show imager
uv run workstation-tidy clean --dry-run
uv run workstation-tidy clean
```

For a command available outside the project, optionally install with `uv tool install .`. Without uv, use `PYTHONPATH=src python3 -m workstation_tidy scan` from the project root, or install the package in a virtual environment.

`clean` requires an interactive terminal. Enter target numbers separated by spaces or commas; nothing is preselected. Review each eligible operation and its consequences, then type `CLEAN`. Enter at the selection prompt or any other confirmation cancels. Unsafe targets are skipped rather than forced. There is no unattended `--yes` flag.

`clean --dry-run` never deletes or writes history. In a terminal it accepts a selection; when redirected it previews all supported candidates. It still performs safety checks, and may report that host process visibility is unavailable.

```sh
uv run workstation-tidy scan --category packages --json
uv run workstation-tidy doctor --json
uv run workstation-tidy config --json
uv run workstation-tidy history
```

JSON reports carry `schema_version: 1`. Target IDs remain stable for a fixed profile; use IDs or exact paths with `show`, not row numbers from an earlier scan. Exit codes are 0 for successful requests (including cancellation/dry-run), 1 for errors or incomplete cleanup, 2 for argument syntax errors, and 130 for interruption. `doctor` returns 1 when process visibility or discovery is incomplete. Candidate-level measurement errors and blocked/missing targets remain visible in scan results.

## What v1 cleans

- npm's `_cacache` through npm; preserves `_npx` and installed dependencies.
- uv package cache, only after ruling out dependent symlinks and centralized environments.
- pip HTTP/wheel caches through pip; retains self-check metadata.
- Recognized Cargo `target` output beside a manifest, after checking Git-tracked files, configuration, desktop launchers and running processes. Explicit arguments constrain both artifact and intermediate directories.
- GNOME thumbnail PNGs in recognized cache directories.
- Raspberry Pi Imager's recognized `lastdownload.cache` file.

The default `usual` profile includes the eight Cargo output paths from the October 2026 cleanup. It also reports JetBrains, Cursor, Chrome, LM Studio and downloaded browser runtimes without deleting them. Missing folders are normal after cleanup and are reported as such.

No root execution, system cleanup, arbitrary command plugins, package uninstallation, Docker pruning, runtime pruning, browser-profile clearing or model deletion. JetBrains Local History and application state are retained by leaving those targets report-only.

## Configuration

Defaults work without a configuration file. The CLI only displays configuration; it never creates or edits it automatically. To customize, create `$XDG_CONFIG_HOME/workstation-tidy/config.toml` (normally `~/.config/workstation-tidy/config.toml`) using [config.example.toml](config.example.toml).

Choose another profile with `--profile NAME`, or another file with `--config /absolute/path.toml`, after the subcommand. An explicitly named missing file uses defaults and is marked `using_defaults` by `config`.

- Paths must be absolute or begin with `~/`, without `..`. Cleanup locations and discovery roots must be below your home directory.
- Omitting `caches` uses all standard cache locations. Supplying the table selects only its entries; `false` disables an entry. Desktop-cache paths must match the standard layout.
- Omitting `cargo_targets` uses the eight historical paths. Set `cargo_targets = []` to opt out.
- `discover_cargo = true` discovers additional manifests with existing `target` folders under `project_roots`, including worktrees. It skips symlinks, other filesystems, dependencies, Git internals and virtual environments, with a depth limit of 12. Limits and discovery failures are reported.
- Exclusions are literal path subtrees. A target containing an excluded child is blocked entirely because owning tools cannot honor arbitrary exclusions.
- Profiles contain locations and preferences only; command strings are rejected.

## Safety and verification boundaries

Scans read directory metadata; they do not inspect personal file contents. Preflight reads relevant manifests/configuration, cache markers, Git file lists and desktop launcher definitions. Target paths, outcomes and measurements are stored; credentials and raw external-tool output are not logged.

Symlinked paths, unknown layouts, inaccessible entries, changed identities, active targets, unsupported Cargo overrides and incomplete process visibility block cleanup. uv dependency inspection walks metadata under your home directory with a 500,000-entry/20-second limit; reaching the limit blocks cleanup. Scans identify candidates, not guarantees that preflight will pass.

Process checks require the host Linux PID namespace. A sandbox, container, `/proc` restrictions or protected processes can prevent verification: `doctor` explains this and cleanup is deferred. Run from your normal host terminal without sudo. The app never escalates or terminates processes.

On the current workstation, the native-host check also found protected session processes that the normal user cannot inspect fully. The strict check currently defers cleanup here even outside the sandbox; read-only scanning and previews remain available. See project status for this unresolved host limitation. There is no bypass flag.

Preflight and path identity are rechecked before each operation; direct desktop deletion uses directory descriptors and no-follow opens. Package-manager commands still operate on paths, and checks cannot eliminate every race with another process changing files or starting after inspection. Close affected applications and builds. This utility is not intended for adversarial concurrently modified directories.

Cleanup is permanent. Cancellation does not undo completed deletions. The last 50 runs are kept under `$XDG_STATE_HOME/workstation-tidy` (normally `~/.local/state/workstation-tidy`). A lock prevents concurrent cleanup runs; history is written atomically before and after operations. A hard termination can leave a run marked `running`; inspect its paths before trying again. Corrupt/unwritable history blocks further cleanup instead of discarding evidence.

Reports distinguish allocated bytes from logical bytes and deduplicate hardlinks. Filesystem free-space deltas are recorded once per device and can differ from estimates because of shared blocks, metadata and concurrent activity. Success means the supported payload is absent; empty cache folders and known metadata can remain. It does not establish that applications were launched or rebuilt successfully.

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build
git diff --check
```

Tests use disposable directories. Real npm, uv, pip and Cargo integration checks run only against fixture caches/build outputs and skip when the required tool is unavailable. Process visibility is simulated for fixture cleanup; live user cleanup is intentionally not part of automated validation.

## Tool references

- [npm cache](https://docs.npmjs.com/cli/commands/npm-cache)
- [uv caching](https://docs.astral.sh/uv/concepts/cache/)
- [pip cache](https://pip.pypa.io/en/stable/cli/pip_cache/)
- [Cargo clean](https://doc.rust-lang.org/cargo/commands/cargo-clean.html)
- [Cargo build directories](https://doc.rust-lang.org/cargo/reference/build-cache.html)

See [project status](docs/project-status.md) for implemented behavior and verification evidence.
