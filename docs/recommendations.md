# Workstation Tidy recommendations

Recorded November 16, 2026. These are proposed priorities for future work; no
implementation is included. Recheck the code and verification evidence before
starting each item.

## Recommended direction

Develop Workstation Tidy into a dependable developer-workstation maintenance
tool. The CLI and TUI already provide discovery, profiles, previews, explicit
confirmation, history, and post-clean verification. The next milestone should
make those foundations trustworthy and explain cleanup decisions clearly.

**Next milestone: safe cleanup, clear explanations, reproducible checks.**

Preserve the existing scan → explain → select → preview → confirm → clean →
verify workflow, with nothing preselected and read-only reporting available.

## First: fix the known safety and reliability issues

The findings below come from [REVIEW.md](../REVIEW.md). Inspection during the
recommendations assessment confirmed that the relevant code was still present.
The report contains the detailed evidence and regression examples; its finding
IDs are used here for traceability.

- [x] **1. Isolate Git inspection from inherited overrides (A1).**
  In `src/workstation_tidy/handlers.py`, Git guards inherit environment settings
  such as `GIT_INDEX_FILE`, `GIT_DIR`, and `GIT_WORK_TREE`. The review demonstrated
  that an alternate empty index could conceal tracked files and permit their
  deletion. Use a dedicated sanitized inspection environment, validate the
  returned repository/worktree relationship, and cover alternate index,
  repository, and worktree settings with real-Git regression tests. Both Cargo
  and project-artifact authorization must reject tracked output.

- [x] **2. Protect nested Git repositories inside Cargo output (A2).**
  Cargo validation in `src/workstation_tidy/handlers.py` lacks the nested Git
  protection already applied to other artifacts. Reuse that protection before
  authorizing Cargo cleanup. Cover `.git` directories, worktree `.git` files,
  and unreadable descendants. Decide explicitly how nested bare repositories
  are handled. Run the Cargo fixture integration with Cargo installed.

  Completed November 17, 2026: inherited Git overrides are isolated and checked
  worktree roots must contain the project. Cargo and artifact checks preserve
  nested `.git` entries and recognizable bare repositories, including checks
  repeated immediately before execution. All 110 tests pass on Python 3.11.18
  and 3.12.14, with no skips, including Cargo 1.91.1 fixture cleanup. C1 is the
  next open review batch; D1/C2/F1 remain open.

- [ ] **3. Restore the inherited Textual APIs (C1).**
  Rename `ScanScreen.visible()` and `action_toggle()` in
  `src/workstation_tidy/tui/screens.py`, updating calls and bindings. They collide
  with inherited APIs. Add a regression for the normal visibility property and
  verify selection/navigation flows and a passing type check.

- [ ] **4. Validate the history schema before cleanup (D1).**
  `src/workstation_tidy/storage.py` currently accepts a list of dictionaries
  without validating nested fields. Malformed result data can crash readers and
  pass the cleanup lock gate. Define the supported record format, including any
  legacy records, and reject invalid structures with `TidyError`. Test valid
  JSON with invalid field shapes, as well as valid history and CLI/TUI readers.

- [ ] **5. Handle oversized size arguments cleanly (C2).**
  Size parsing in `src/workstation_tidy/cli.py` can overflow through float
  conversion. Reject oversized values with the normal argument diagnostic.
  Cover CLI exit behavior and TUI validation without a traceback.

- [ ] **6. Enforce uv inspection budgets while iterating (F1).**
  `cache_link_check` in `src/workstation_tidy/safety.py` materializes whole
  directory listings before checking entry/time limits. Iterate entries directly
  and enforce budgets during traversal. Add a counting-iterator regression.
  Preserve the rule that incomplete inspection blocks cleanup; document that
  one blocking filesystem operation cannot have a strict wall-clock bound in
  the current worker model.

## Then: improve everyday usefulness

- [ ] **Explain blocked targets with an actionable next step.**
  In CLI and TUI details, show the specific reason, relevant path or dependency,
  and the smallest safe action a user can take. uv is a useful first case:
  cached environments and managed-Python links are documented blockers, not
  reasons to bypass dependency checks. Verify the current host state before
  describing any blocker as current.

- [ ] **Make configuration portable.**
  Move the eight historical Cargo locations out of the default experience into
  an explicit personal profile. Provide guided project-root configuration and
  discovery so another developer can adopt the tool without inheriting those
  locations. Preserve existing custom configuration behavior.

- [ ] **Make maintenance results easier to follow over time.**
  Add useful summaries for verified reclaimed space, partial failures, recurring
  large targets, and scan comparisons. Keep target-size estimates distinct from
  measured filesystem free-space changes. Scan comparisons may need separate
  snapshot storage because cleanup history alone does not record every scan.

## Release evidence and documentation

- [ ] Pin development checker versions and make lint, formatting, type checking,
  tests, and package builds reproducible.
- [ ] Add CI covering Python 3.11 and a newer supported interpreter. Ensure a
  Cargo-enabled job actually executes its integration rather than skipping it.
- [ ] Test Windows reporting natively, including junction handling on the
  declared Python floor. Linux simulations are not native Windows evidence.
- [ ] Exercise the TUI in a real terminal after the API fixes. Headless tests
  remain useful but do not establish the complete terminal experience.
- [ ] Refresh [project-status.md](project-status.md) after fixes. Its 68-test and
  type-check results are historical; its next-step text still points to a
  process-visibility blocker that the same document records as resolved.
  Distinguish current checks, historical results, simulations, and skipped or
  unverified native behavior.

## Verification snapshot

During the November 16 recommendations assessment,
`.venv/bin/python -m unittest discover -s tests` ran 101 tests successfully,
with no skips reported. These passing tests do not cover all review findings.

`.venv/bin/python -m mypy src` could not run because mypy was absent from the
local environment. The type-check failure described above comes from the
committed review, supported by inspection of the unchanged methods. The review
also records separate Python 3.11 verification; that was not rerun during this
assessment. No live cleanup of personal data was performed.

## Scope for later

A graphical GUI, scheduling, and additional deletion categories can follow
after the safety-and-reliability milestone. Privileged/system cleanup,
browser-profile clearing, runtime pruning, and model deletion remain outside
the current v1 boundary. Expanding those areas should be a separate design
decision with its own safety and verification requirements.
