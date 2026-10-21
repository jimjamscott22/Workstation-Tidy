# Workstation Tidy project status

Updated October 21, 2026. Version 0.1.0 implements the approved user-level Python CLI design.

## Implemented

- `scan`, `show`, `clean`, `doctor`, `history`, and `config`, with profile selection and read-only JSON reports.
- Numbered interactive selection, no preselection, exact operation previews, explicit `CLEAN` confirmation, dry-run and non-TTY deletion refusal.
- Default npm/uv/pip, Cargo, thumbnails and Pi Imager candidates, the eight historical Cargo locations, report-only application directories, custom TOML profiles, subtree exclusions and optional Cargo/worktree discovery.
- Allocated/logical measurements, hardlink deduplication, sparse-file accounting, no-follow traversal, filesystem boundaries, explicit incomplete-scan errors and stable target IDs.
- Owning-tool cleanup for npm/uv/pip/Cargo; recognized desktop cache files use directory descriptors. Imager OS-list metadata is retained. Expected uv cache-internal links are measured without traversal; external links and environment dependencies block cleanup.
- Identity, ownership, layout, Git-tracked-output, Cargo configuration, launcher and active-process checks. Unverifiable process visibility blocks cleanup rather than assuming inactivity.
- Sequential execution, single-instance lock, atomic bounded history, interruption results, partial/failure/skip reporting and per-filesystem free-space measurements.
- Standard-library-only runtime, Python 3.11+ package and console entry point; uv development environment and lockfile.

## Verification

- 56 unittest tests passed on Python 3.14.4, with warnings treated as errors.
- Four real-tool fixture integrations passed: npm, uv, pip and Cargo. These only removed disposable fixture data and checked that installed dependencies, source/lockfiles, environments or retained metadata survived.
- Ruff lint/format checks and mypy passed for the source.
- Wheel and source-distribution build passed; packaged command and read-only workstation reports were exercised.
- Read-only native-host `doctor` confirmed installed tools and correctly reported inaccessible protected session processes. Sandbox `doctor` separately detects the private PID namespace.

## Limits and remaining evidence

- No real user cache or build output was deleted during implementation. No native-host cleanup of personal data was performed.
- **Current host blocker:** protected session processes (including the session manager/PAM helper and ssh-agent) cannot be fully inspected as the normal user. `doctor` reports their PIDs; the strict visibility rule currently defers cleanup on this host. Running outside the sandbox alone does not eliminate this restriction. There is no override to suppress the check.
- Fixture cleanup tests simulate a complete process view. They prove selection/execution boundaries and tool behavior, not that the host's protected-process visibility issue is resolved.
- Process/path checks are point-in-time, not an adversarial race-proof transaction around external tools. Close affected apps and builds. No process termination, privilege escalation or recursive-deletion fallback is implemented.
- uv metadata dependency scans have limits and block on incomplete inspection. Cargo configuration overrides, unknown generated layouts and launcher-linked outputs are intentionally conservative.
- A hard kill can leave a history run marked `running`. Cancellation cannot restore deleted files. Individual external tools are not forcibly timed out mid-deletion.
- Python 3.11 is the declared floor; execution was verified on the installed Python 3.14.4, not a Python-version matrix.

## Next step

Use the read-only scan/show commands to review the profile. Before expanding live cleanup, design a narrowly scoped solution for protected-process inspection; do not weaken the check silently. Privileged/system cleanup, browser/profile clearing, runtime pruning, scheduling and a GUI remain outside v1.
