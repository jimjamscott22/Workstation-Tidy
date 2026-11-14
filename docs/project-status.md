# Workstation Tidy project status

Updated November 13, 2026. Version 0.1.0 implements the approved user-level Python CLI design, plus project-artifact discovery.

## Implemented

- `scan`, `show`, `clean`, `doctor`, `history`, and `config`, with profile selection and read-only JSON reports.
- Numbered interactive selection, no preselection, exact operation previews, explicit `CLEAN` confirmation, dry-run and non-TTY deletion refusal.
- Default npm/uv/pip, Cargo, thumbnails and Pi Imager candidates, the eight historical Cargo locations, report-only application directories, custom TOML profiles, subtree exclusions and optional Cargo/worktree discovery.
- Project-artifact discovery (on by default): `node_modules` with a lockfile, virtualenvs containing `pyvenv.cfg`, Python tool caches, and named frontend build folders can be cleaned on Linux after the same preview and `CLEAN` confirmation. Ambiguous `dist`/`build`/`out`/`coverage`/`htmlcov` directories are reported and never deleted. `scan` and `show` include age, parent project, and marker. `--older-than` filters by newest mtime.
- Windows identification: `scan`, `show`, `doctor`, `history`, and `config` run without `os.geteuid` or `/proc`. `clean` and `tui` still require Linux. Windows measurement uses `os.scandir` and `os.stat` (not `DirEntry.stat`, whose device and inode are zero on Windows) and does not authorize deletion.
- Artifact cleanup blocks when a nested Git repository, including a `.git` file, is inside the selected folder. The parent `git ls-files` index does not see that history.
- Allocated/logical measurements, hardlink deduplication, sparse-file accounting, no-follow traversal, filesystem boundaries, explicit incomplete-scan errors and stable target IDs.
- Owning-tool cleanup for npm/uv/pip/Cargo; recognized desktop cache files use directory descriptors. Imager OS-list metadata is retained. Expected uv cache-internal links are measured without traversal; external links and environment dependencies block cleanup.
- Identity, ownership, layout, Git-tracked-output, Cargo configuration, launcher and active-process checks. Unverifiable process visibility blocks cleanup rather than assuming inactivity.
- Sequential execution, single-instance lock, atomic bounded history, interruption results, partial/failure/skip reporting and per-filesystem free-space measurements.
- Optional Textual TUI (`workstation-tidy tui`, `tui` extra): filterable scan table with detail pane, selection by stable target ID, preflight preview, typed `CLEAN` confirmation, live per-target results, stop-after-current, doctor/history/config views and `--dry-run`. It reuses the core preflight and `service.clean`; no checks are added or bypassed.
- Standard-library-only CLI runtime, Python 3.11+ package and console entry point; uv development environment and lockfile.

## Verification

- 68 unittest tests passed on Python 3.14.4 (including headless Textual TUI tests), with warnings treated as errors.
- Four real-tool fixture integrations passed: npm, uv, pip and Cargo. These only removed disposable fixture data and checked that installed dependencies, source/lockfiles, environments or retained metadata survived.
- Ruff lint/format checks and mypy passed for the source.
- Wheel and source-distribution build passed; packaged command and read-only workstation reports were exercised.
- Read-only native-host `doctor` confirmed installed tools and correctly reported inaccessible protected session processes. Sandbox `doctor` separately detects the private PID namespace.

## Limits and remaining evidence

- No real user cache or build output was deleted during implementation. No native-host cleanup of personal data was performed.
- **Resolved host blocker (2026-11-11):** non-dumpable session daemons (`systemd --user`, `(sd-pam)`, `ssh-agent`) previously blocked all cleanup. A fixed allowlist, matched by process name and command line, now accepts them and lists them as not inspected. Any other process that cannot be inspected still blocks. Native-host `doctor` now reports visibility as verified.
- uv cleanup on this host remains blocked by existing checks: cached `uvx`/`uv run --with` environments in `environments-v2`, and `archive-v0/*/bin/python` links to uv-managed Python installs.
- Fixture cleanup tests simulate a complete process view. They prove selection/execution boundaries and tool behavior, not that the host's protected-process visibility issue is resolved.
- Process/path checks are point-in-time, not an adversarial race-proof transaction around external tools. Close affected apps and builds. No process termination, privilege escalation or recursive-deletion fallback is implemented.
- uv metadata dependency scans have limits and block on incomplete inspection. Cargo configuration overrides, unknown generated layouts and launcher-linked outputs are intentionally conservative.
- A hard kill can leave a history run marked `running`. Cancellation cannot restore deleted files. Individual external tools are not forcibly timed out mid-deletion.
- Python 3.11 is the declared floor; execution was verified on the installed Python 3.14.4, not a Python-version matrix.

## Next step

Use the read-only scan/show commands to review the profile. Before expanding live cleanup, design a narrowly scoped solution for protected-process inspection; do not weaken the check silently. Privileged/system cleanup, browser/profile clearing, runtime pruning, scheduling and a graphical (non-terminal) GUI remain outside v1.
