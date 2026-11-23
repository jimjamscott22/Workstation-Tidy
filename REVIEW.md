# Workstation Tidy review

## Batch 1 resolution (November 17, 2026)

A1 and A2 are addressed by the deletion-authorization changes on this branch.
Git inspection now uses a sanitized environment and validates its worktree root;
Cargo and artifact cleanup both reject nested `.git` entries and recognizable
bare repositories. Execution repeats these checks. Nine real-Git authorization
tests were added; all 110 tests pass on Python 3.11.18 and 3.12.14, without skips,
including the four real-tool integrations with Cargo 1.91.1 available. An
independent review found no remaining defects in this scoped batch.

C1, D1, C2 and F1 remain open; Batch 2 (C1, Textual API compatibility) is next.
Mypy still reports C1's two existing override errors. The original review and
baseline evidence below describe the reviewed commit, not the fixed branch.

## Summary

Reviewed commit `a4ed501663daaea9156221669f4398f81e40bb6d` on Linux, using Python 3.12.14 and a separate Python 3.11.18 environment. The core is small and clearly separated, with useful fail-closed measurement, exclusion, identity, process-visibility and history safeguards. The existing suite passes on both interpreters (100 passed, one Cargo integration skipped per run), but it misses two deletion-safety failures: inherited Git environment settings can conceal tracked files, and Cargo output validation can authorize removal of nested Git history. Mypy also fails against the locked Textual dependency. This is a review only: no fixes, source/test/manifest edits, commits, environment configuration updates or publication were performed. The only repository change is this report; the only review-driven deletion was inside a disposable fixture with HOME and all three XDG directories redirected inside it.

**Top three fixes:**

1. **A1:** Isolate Git inspection from inherited index/repository overrides before trusting an empty tracked-file listing.
2. **A2:** Apply nested-repository protection to Cargo targets, including `.git` files and directories.
3. **C1:** Rename the TUI methods that override Textual APIs; restore a passing type check with regression coverage.

### Recon: flow and safety map

| Stage | Implementation and responsibility |
| --- | --- |
| Configuration | `src/workstation_tidy/config.py:119`, `:135`: validate paths, home containment, key types, exclusions and configured-target overlap. |
| Discovery and measurement | `src/workstation_tidy/scanner.py:350`, `:452`: bounded project discovery, artifact classification, overlap rejection and deterministic hardlink attribution. `:50`, `:107`, `:198`: canonical identity, ownership, no-follow sizing and platform-specific traversal. |
| Candidate presentation | `src/workstation_tidy/common.py:71`: scan, layout check and operation availability. CLI `src/workstation_tidy/cli.py:125` and TUI `src/workstation_tidy/tui/screens.py:340` perform selection and preview, followed by explicit confirmation. |
| Preflight | `src/workstation_tidy/handlers.py:59`: identity, exclusions, fresh measurement, process checks, layout and tool-specific safeguards. `src/workstation_tidy/safety.py:61` inventories process references; `:130`, `:168`, `:218` constrain Cargo configuration, uv dependencies and launchers. |
| Execution | `src/workstation_tidy/service.py:105` repeats preflight before execution. `src/workstation_tidy/handlers.py:334` repeats identity/layout validation, then uses explicit tool arguments or descriptor-based deletion (`:359`, `:405`). |
| Verification and history | `src/workstation_tidy/service.py:139` remeasures and checks supported payload; `:62` holds a lock and saves before deletion and after each result. `src/workstation_tidy/storage.py:33`, `:53` implement flock and atomic, fsynced history replacement. |

The README, project-status document and implementation plan were read before the review. No AGENTS.md instructions were found under `/workspace`.

## Baseline results

All commands ran from `/workspace/Workstation-Tidy`. Shell setup was:

```sh
export UV_CACHE_DIR=/workspace/.cache/uv
export PATH=/workspace/.tools/workstation-tidy/bin:$PATH
```

These reuse the published setup: the default uv cache is read-only and the base-image pip launcher has an invalid build-time shebang. The existing local tools environment supplies a working pip3. No global installation or dependency upgrade was performed. Since Ruff and mypy were absent, they were added **only to `.venv`** with `uv pip install --python .venv/bin/python ruff mypy`; this added Ruff 0.17.5, mypy 2.4.1 and their dependencies without changing `pyproject.toml` or `uv.lock`. uv is 0.13.6; Textual is locked at 8.2.8. These checker versions are review-local, not project-pinned.

| Command | Exit | Result |
| --- | --- | --- |
| `uv sync --extra tui` | 0 | Locked application dependencies installed; editable package rebuilt. |
| `uv run python -m unittest discover -s tests -v` | 0 | 101 tests run: 100 passed, one skipped (`Cargo/git unavailable`; Git is present, Cargo is absent). Headless TUI and real npm/uv/pip fixture integrations executed. |
| `uv run ruff check . && uv run ruff format --check .` | 0 | `All checks passed!`; `24 files already formatted`. |
| `uv run mypy src` | 1 | Two incompatible Textual overrides in `tui/screens.py:178` and `:306`; C1 below. |
| `uv build` | 0 | sdist and wheel built successfully. |
| `uv run --python 3.11 --locked --extra tui python -m unittest discover -s tests -v` | 0 | Python 3.11.18: 101 tests run, 100 passed and the same Cargo skip. Separate `/tmp/workstation-review-py311` environment; project environment and lockfile retained. |
| `git diff --check` | 0 | Passed before report creation. |

For the Python 3.11 run, additionally set `UV_PYTHON_INSTALL_DIR=/workspace/.tools/python` and `UV_PROJECT_ENVIRONMENT=/tmp/workstation-review-py311`. Downloaded the interpreter through uv's verified normal installation route. The cache-to-`/tmp` hardlink fallback warning is an environment filesystem limitation, not a test failure. Textual's asyncio debug logs report some slow task callbacks; they are not failing assertions. Neither run used warnings-as-errors. Windows execution, actual Cargo execution and native-host cleanup were not verified here. Host process visibility is unavailable in this container, as established during onboarding; review fixtures inject a complete ProcessView and do not establish native-host inactivity.

Exact baseline stdout/stderr, including timings and warnings, is preserved at the end of this report so it does not depend on retained temporary logs.

## Findings

Severity follows the supplied rule: any path that can delete the wrong data is **Critical**, regardless of how often its prerequisite occurs. Confirmed means a reproduced failure or a precise code path; limitations on external-tool execution are stated explicitly.

### a. Deletion safety

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A1 | Critical | Confirmed | `src/workstation_tidy/handlers.py:186`, `:191`, `:229`, `:232`; inherited environment at `:37` | Both Git guards trust subprocesses that inherit `GIT_INDEX_FILE`, `GIT_DIR`, `GIT_WORK_TREE` and related Git overrides. `git -C` does not neutralize them; the reported repository root is not checked either. An alternate empty index makes a genuinely tracked artifact appear untracked. | In a disposable Git repository, `git add node_modules/precious.js` made the normal index list the file; setting `GIT_INDEX_FILE` to an absent alternate-index path let full `Handler.preflight` pass and real `Handler.execute` delete it with exit 0. This does not require a path race, symlink or malicious concurrent writer. Cargo uses the same unsafe Git inspection pattern. | Use a dedicated, sanitized Git-inspection environment and verify the returned worktree contains the project. Strip repository/index overrides and configuration injection that can redirect inspection; add real-Git tests with alternate indices and alternate repository/worktree settings. Keep the ordinary developer environment out of deletion authorization. | M |
| A2 | Critical | Confirmed | `src/workstation_tidy/handlers.py:180`, `:191`, `:307`; missing equivalent of `:239` | Cargo validation looks for Cargo.toml/Cargo.lock and tracked paths, but never runs the nested `.git` check. A non-Rust repository under `target/debug/vendor` passes the layout checks even with local-only history. | Parent `git ls-files -- target` cannot see a nested repository's independent index/history. A full `cargo clean --target-dir <target>` authorizes removing that tree, including irreplaceable nested source and commits. The regression fixture with a valid outer Rust package and an inner committed Python file passed `check_layout`. Actual Cargo deletion was **not run** because Cargo is absent; command scope follows `preview` at lines 307–320. | Reuse the fail-closed nested-repository traversal for Cargo targets before preview/execute. Cover `.git` directories, worktree `.git` files, unreadable descendants and nested bare repositories if those are intended to be protected. | S |

A1 was demonstrated using real internal artifact deletion only inside a temporary HOME. A2 was demonstrated through the real layout/Git guard, without deleting its nested history. Executable failing regressions for both are included below.

Reviewed protections that held: canonical-path checks reject symlinked ancestors; exclusions reject ancestor/descendant overlap; artifact lockfiles and pyvenv.cfg use no-follow regular-file checks; artifact symlinks are unlinked rather than followed; descriptor deletion checks identity, ownership and device. Descriptor child-open races and path-based package-manager races remain possible under concurrent modification, but the README expressly excludes adversarial mutation; this review does not present that documented boundary alone as a new bug.

### b. Process-visibility logic

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |

No additional confirmed finding within the documented non-adversarial model. Reviewed `src/workstation_tidy/safety.py:32`, `:61`, `:95`, `:107`: an ordinary unreadable same-user process adds an error, and ProcessView.check blocks every target when errors exist. Allowlisted opaque daemons are reported separately. The name/argv prefix check is **not executable authentication** and PID 1's name is **not proof of a host namespace**; same-user process spoofing and containers running systemd remain trust-boundary limitations. No native-host or adversarial-process exploit was validated, so neither is counted as a confirmed finding. Decide whether stronger executable/namespace evidence is desired before claiming an adversarial guarantee.

### c. Correctness

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C1 | Med | Confirmed | `src/workstation_tidy/tui/screens.py:178`, `:306` | `ScanScreen.visible()` overrides DOMNode's boolean `visible` property; `action_toggle()` also overrides an inherited action with an incompatible signature. Both fail mypy. | This is an API collision, not a style issue. `ScanScreen().visible` is a bound method instead of a boolean. Assigning the supported DOM property `screen.visible = False` shadows the method; the next `self.visible()` call at line 192 raises TypeError. The base toggle action also expects an attribute-name argument. Ordinary covered navigation still passes. | Rename to e.g. `visible_candidates` and `action_toggle_candidate`, updating local calls/bindings; retain the inherited APIs. Add a visibility/property regression and rerun mypy plus headless flows. | S |
| C2 | Low | Confirmed | `src/workstation_tidy/cli.py:29`, `:34`, `:197`; TUI caller `src/workstation_tidy/tui/screens.py:289` | Size parsing converts an arbitrary-length decimal to float and then int without a finite/range check. A syntactically matched number can overflow. | `size_argument('9' * 400 + 'MiB')` raises `OverflowError: cannot convert float infinity to integer`. Argument parsing happens before main's try block, so CLI usage gets a traceback instead of the documented argument-error exit 2. In the TUI the handler catches ValueError, not OverflowError. | Parse with bounded integer/decimal arithmetic or reject non-finite/oversized values using ArgumentTypeError. Validate multiplication overflow and add large-input CLI/TUI negative cases. | S |

JSON emission consistently adds schema_version=1 on the inspected normal/error paths. Path-derived discovery IDs are deterministic for unchanged paths. Size deduplication and newest-mtime filtering have existing meaningful tests; estimates are not guarantees of reclaimable blocks. No unsupported claim of native Windows correctness is made.

### d. State and concurrency

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | Med | Confirmed | `src/workstation_tidy/storage.py:26`, `:48`; readers `src/workstation_tidy/cli.py:236`, `src/workstation_tidy/tui/screens.py:656` | History validation only checks list-of-dictionaries. Invalid nested fields pass the lock gate, although the readers assume result objects and iterable results. | `[{'results': 'invalid'}]` is accepted by Store.read and Store.lock; placing that JSON at the fixture state path makes `main(['history'])` raise an uncaught TypeError. Cleanup is allowed to start with this malformed history rather than refusing with the promised corrupt-history diagnostic. | Validate the supported run/result schema before authorizing cleanup; convert malformed records to TidyError. If minimal legacy records must be supported, define that format and validate any present fields rather than demanding all modern fields. Add valid-JSON/invalid-schema cases. | M |

The flock lifetime and atomic replacement are appropriate. A run left `running` after a hard kill is documented and does not itself prove a concurrency bug. Ctrl-C handling inside preflight, execution and verification is tested, but signals during persistence can still leave the documented incomplete record. No claim that interruption rolls back completed deletion is made.

### e. Cross-platform

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |

No additional confirmed platform defect. Reviewed platform gates in `src/workstation_tidy/common.py:33`, `src/workstation_tidy/safety.py:63`, lazy fcntl import in `src/workstation_tidy/storage.py:37`, and the statvfs fallback in `src/workstation_tidy/service.py:26`. The Linux-run Windows simulations pass; they are not native Windows evidence. In particular, native junction behavior on Python 3.11 remains unverified: `src/workstation_tidy/scanner.py:33` uses Path.is_junction only when available, and that API was introduced in 3.12. Treat junction identification on the declared floor as an open validation question, not an established data-loss finding (cleanup is Linux-only).

### f. Performance

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | Med | Confirmed | `src/workstation_tidy/safety.py:183`, `:184`, `:191` | uv's dependency scan materializes an entire directory into a list before enforcing its entry/time limits. | An injected 10,000-entry iterator with max_entries=1 was fully consumed before the limit error. A very wide or slow directory can allocate arbitrarily much listing memory and exceed the nominal deadline before the first budget check. It still blocks cleanup eventually; this is a resource/responsiveness issue, not a demonstrated wrong deletion. | Iterate scandir entries directly within the context manager, checking the budget before scheduling each item. Document that a single blocking filesystem operation cannot have a strict wall-clock bound without an isolated/cancellable worker. Add a counting-iterator regression. | S |

Scans, preflight, reports and cleanup are dispatched to Textual thread workers at `src/workstation_tidy/tui/screens.py:131`, `:366`, `:518`, `:636`. No primary synchronous UI-thread traversal was found. Repeated full measurements serve freshness checks; no performance improvement is recommended that simply removes those safety checks. Discovery can walk project roots separately for Cargo and artifacts (`src/workstation_tidy/scanner.py:455`, `:459`), but large-tree runtime was not benchmarked here.

### g. Tests

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |

No separate duplicate finding. Important coverage gaps demonstrated by A1/A2/D1 are located in `tests/test_safety.py:280` (mocked tracked-output replies), `tests/test_safety.py:289` (nested Git only for artifact handlers), `tests/test_tool_integration.py:69` (Cargo fixture has no nested repository), and `tests/test_service.py:141` (corrupt JSON syntax only). The three real-Git/schema regressions below fail on the reviewed commit. Tests that inject ProcessView are correctly described by the README as fixture safety tests; they should not be presented as native process-visibility proof. A Cargo-enabled validation job is still needed before merging a Cargo fix; its skipped integration was not counted as passed.

### h. Documentation drift

| ID | Severity | Status | File:Line | Problem | Why it matters | Suggested fix | Effort |
| --- | --- | --- | --- | --- | --- | --- | --- |

No separate finding beyond the contract gaps already recorded: D1 conflicts with the README's corrupt-history refusal, and F1 weakens the practical interpretation of its uv scan limits. `docs/project-status.md:22` and `:24` record historical 68-test/type-check results; the current evidence is 101 tests with two mypy errors using the versions above. The older dated results are not proof that the historical run was false. After fixes, refresh the evidence with exact interpreter/checker versions and distinguish native Windows/Cargo execution from simulated or skipped coverage.

### Reproducible safety/state regressions

Run from the repository root with the published cache/PATH exports. These tests **do not execute cleanup**; each asserts that authorization must reject the fixture. The intended assertions fail on this commit. All fixture paths and HOME/XDG bindings are temporary. ProcessView is deliberately injected to isolate layout/Git authorization, not to bypass visibility for live user data.

```sh
PYTHONPATH=src uv run python - <<'PYTEST'
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workstation_tidy.config import Profile
from workstation_tidy.handlers import Handler
from workstation_tidy.models import Target, TidyError
from workstation_tidy.safety import ProcessView
from workstation_tidy.scanner import inspect
from workstation_tidy.storage import Store

class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tidy-review-regression-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        scoped = {'HOME':str(self.home)}
        scoped.update({key:str(self.home/key) for key in (
            'XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_CACHE_HOME')})
        patcher = patch.dict(os.environ,scoped)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = Handler(Profile('fixture',self.home,(),(),(),False))
    def git(self,p,*args):
        return subprocess.run(['git','-C',str(p),*args],check=True,capture_output=True,text=True)
    def test_tracked_artifact_rejected_with_alternate_index(self):
        p=self.home/'app'; p.mkdir(); self.git(p,'init','-q')
        (p/'package.json').write_text('{}'); (p/'package-lock.json').write_text('{}')
        target=p/'node_modules'; target.mkdir(); (target/'source.js').write_text('precious')
        self.git(p,'add','node_modules/source.js')
        c=inspect(Target('fixture','node_modules',target,'build','fixture'))
        with patch.dict(os.environ,{'GIT_INDEX_FILE':str(self.home/'alternate-index')}):
            with self.assertRaises(TidyError):
                self.handler.preflight(c,ProcessView())
    def test_cargo_nested_git_rejected_by_layout(self):
        p=self.home/'cargo'; (p/'src').mkdir(parents=True); self.git(p,'init','-q')
        (p/'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n')
        (p/'src/lib.rs').write_text('pub fn fixture() {}')
        target=p/'target'; nested=target/'debug/vendor'; nested.mkdir(parents=True)
        (target/'CACHEDIR.TAG').write_text('Signature: 8a477f597d28d172789f06886806bc55\n')
        self.git(nested,'init','-q'); (nested/'source.py').write_text('precious')
        self.git(nested,'add','source.py')
        self.git(nested,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','local only')
        with self.assertRaises(TidyError):
            self.handler.check_layout(Target('fixture','cargo',target,'build','fixture'))
    def test_malformed_history_blocks_lock(self):
        p=self.home/'state'; p.mkdir(); (p/'history.json').write_text('[{"results":"invalid"}]')
        with self.assertRaises(TidyError):
            with Store(p).lock():
                pass
if __name__=='__main__': unittest.main(verbosity=2)
PYTEST
```

Exact regression output:

```text
test_cargo_nested_git_rejected_by_layout (__main__.ReviewRegressions.test_cargo_nested_git_rejected_by_layout) ... FAIL
test_malformed_history_blocks_lock (__main__.ReviewRegressions.test_malformed_history_blocks_lock) ... FAIL
test_tracked_artifact_rejected_with_alternate_index (__main__.ReviewRegressions.test_tracked_artifact_rejected_with_alternate_index) ... FAIL

======================================================================
FAIL: test_cargo_nested_git_rejected_by_layout (__main__.ReviewRegressions.test_cargo_nested_git_rejected_by_layout)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/workstation-review-regressions.py", line 46, in test_cargo_nested_git_rejected_by_layout
    with self.assertRaises(TidyError):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: TidyError not raised

======================================================================
FAIL: test_malformed_history_blocks_lock (__main__.ReviewRegressions.test_malformed_history_blocks_lock)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/workstation-review-regressions.py", line 50, in test_malformed_history_blocks_lock
    with self.assertRaises(TidyError):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: TidyError not raised

======================================================================
FAIL: test_tracked_artifact_rejected_with_alternate_index (__main__.ReviewRegressions.test_tracked_artifact_rejected_with_alternate_index)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/workstation-review-regressions.py", line 35, in test_tracked_artifact_rejected_with_alternate_index
    with self.assertRaises(TidyError):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: TidyError not raised

----------------------------------------------------------------------
Ran 3 tests in 0.043s

FAILED (failures=3)
```

The stronger A1 execution experiment used the same tracked node_modules fixture, the alternate-index environment and scoped HOME/XDG bindings, then called the real `handler.execute(candidate, handler.preflight(candidate, ProcessView()))`. Its exact output was:

```text
Tracked-file fixture execute exit: 0
Tracked-file fixture exists after execute: False
```

D1's plain-history reader was separately reproduced using `XDG_STATE_HOME=<temporary-home>/state` and writing the invalid record to `state/workstation-tidy/history.json`; `main(['history'])` raised `TypeError: string indices must be integers, not 'str'`.

F1's iterator-budget probe:

```python
from pathlib import Path
from unittest.mock import patch
from workstation_tidy.safety import cache_link_check
from workstation_tidy.models import TidyError


class Entry:
    def is_symlink(self):
        return False

    def is_dir(self, *, follow_symlinks):
        return False


class Listing:
    count = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        for _ in range(10_000):
            self.count += 1
            yield Entry()


listing = Listing()
with patch("workstation_tidy.safety.os.scandir", return_value=listing):
    try:
        cache_link_check(Path("/fixture/cache"), Path("/fixture"), max_entries=1)
    except TidyError as exc:
        print(exc)
print(listing.count)  # observed 10000, before budget rejection
```

## Feature suggestions

| ID | Feature | Value | Effort | Notes |
| --- | --- | --- | --- | --- |
| FS1 | Explain blocked candidates with stable reason codes | Makes JSON consumers and tests independent of prose. | S | Additive schema fields; preserve human explanations and fail-closed checks. |
| FS2 | Read-only interrupted-run inspection | Help users reconcile stale `running` records against current paths. | M | Never silently assume completion or authorize a retry; show uncertainty and recorded results. |
| FS3 | Show selected-but-filtered targets in TUI | Makes retained selections obvious after changing filters. | S | Preview still lists every target; this improves selection clarity without changing authorization. |
| FS4 | Opt-in read-only scan timing/counters | Diagnose large trees and distinguish discovery, sizing, Git and dependency-scan costs. | S | Aggregate counts/timings only; do not log file contents or process arguments. |

## Suggested fix order

1. **Batch 1 — deletion authorization:** Fix A1 and A2 together with real-Git regressions. Exercise alternate index/worktree settings and nested `.git` files/directories. Run a Cargo-enabled fixture integration on valid outer Rust projects. Never weaken process visibility to run live cleanup in the cloud container.
2. **Batch 2 — TUI compatibility:** Fix C1 and add inherited-property/action compatibility checks. Run Ruff, mypy and the existing headless TUI suite against the locked Textual version.
3. **Batch 3 — state integrity:** Fix D1, covering malformed nested fields, supported legacy records, readable history, and refusal before any handler execution. Preserve unreadable/corrupt evidence instead of overwriting it.
4. **Batch 4 — bounded inspection and parsing:** Fix F1 and C2 with counting-iterator and oversized-input tests. Recheck that budget failures remain safe skips and CLI syntax errors return 2.
5. **Batch 5 — platform evidence and documentation:** Run native Windows/Python 3.11 junction fixtures and a Cargo-enabled Linux job. Re-run both Python suites, type/lint checks and build; update verification evidence with versions, skips and limits. Consider feature suggestions only after the safety fixes.

## Questions for the author

1. Should nested **bare** repositories and locally modified vendored source be protected in every destructive cache handler, in addition to `.git` files/directories? A2 demonstrates that the existing nested-repository rule is not consistently applied to Cargo.
2. Which legacy history shapes must remain readable? Current tests intentionally save minimal `{'id': ...}` records (`tests/test_service.py:134`), so D1 needs an explicit compatibility rule rather than blindly requiring every modern field.
3. Is the daemon/PID-namespace trust model intentionally heuristic and non-adversarial? If executable identity or reliable container detection is required, name/argv-prefix and PID 1 name checks need stronger evidence.
4. Is native Windows identification on Python 3.11 a release requirement, including junctions? This review verified the interpreter floor on Linux, not Windows reparse points.

## Exact baseline output

The following blocks contain the captured combined stdout/stderr for this review's runs, without truncation. No production cleanup command was used.

<details>
<summary>uv sync --extra tui</summary>

```text
Resolved 10 packages in 5ms
   Building workstation-tidy @ file:///workspace/Workstation-Tidy
      Built workstation-tidy @ file:///workspace/Workstation-Tidy
Prepared 1 package in 794ms
Uninstalled 1 package in 0.67ms
Installed 1 package in 2ms
 ~ workstation-tidy==0.1.0 (from file:///workspace/Workstation-Tidy)
```

</details>

<details>
<summary>Review-local checker installation</summary>

```text
Resolved 7 packages in 561ms
Downloading mypy (14.8MiB)
Downloading ruff (10.1MiB)
Downloading ast-serialize (1.3MiB)
 Downloaded ast-serialize
 Downloaded ruff
 Downloaded mypy
Prepared 6 packages in 490ms
Installed 6 packages in 26ms
 + ast-serialize==0.13.1
 + librt==0.16.0
 + mypy==2.4.1
 + mypy-extensions==1.2.0
 + pathspec==1.1.1
 + ruff==0.17.5
```

</details>

<details>
<summary>uv run python -m unittest discover -s tests -v</summary>

```text
   Building workstation-tidy @ file:///workspace/Workstation-Tidy
      Built workstation-tidy @ file:///workspace/Workstation-Tidy
Uninstalled 1 package in 0.45ms
Installed 1 package in 1ms
test_clean_preview_names_session_daemons_not_inspected (test_cli.CliTests.test_clean_preview_names_session_daemons_not_inspected) ... ok
test_confirmation_cancel_does_not_execute (test_cli.CliTests.test_confirmation_cancel_does_not_execute) ... ok
test_confirmed_fixture_cleanup_and_history (test_cli.CliTests.test_confirmed_fixture_cleanup_and_history) ... ok
test_doctor_lists_session_daemons_not_inspected (test_cli.CliTests.test_doctor_lists_session_daemons_not_inspected) ... ok
test_dry_run_no_cleanup_or_history (test_cli.CliTests.test_dry_run_no_cleanup_or_history) ... ok
test_invalid_selection_and_sizes (test_cli.CliTests.test_invalid_selection_and_sizes) ... ok
test_json_scan_read_only (test_cli.CliTests.test_json_scan_read_only) ... ok
test_no_selection_does_not_delete (test_cli.CliTests.test_no_selection_does_not_delete) ... ok
test_noninteractive_cleanup_refused (test_cli.CliTests.test_noninteractive_cleanup_refused) ... ok
test_older_than_filters_on_newest_mtime (test_cli.CliTests.test_older_than_filters_on_newest_mtime) ... ok
test_root_refusal (test_cli.CliTests.test_root_refusal) ... ok
test_show_explains_consequences (test_cli.CliTests.test_show_explains_consequences) ... ok
test_size_and_category_filters (test_cli.CliTests.test_size_and_category_filters) ... ok
test_windows_cleanup_is_refused (test_cli.CliTests.test_windows_cleanup_is_refused) ... ok
test_windows_readonly_scan_does_not_call_geteuid (test_cli.CliTests.test_windows_readonly_scan_does_not_call_geteuid) ... ok
test_artifact_discovery_defaults_on_and_can_be_disabled (test_config.ConfigTests.test_artifact_discovery_defaults_on_and_can_be_disabled) ... ok
test_commands_not_allowed (test_config.ConfigTests.test_commands_not_allowed) ... ok
test_custom_profile_cache_and_exclusions (test_config.ConfigTests.test_custom_profile_cache_and_exclusions) ... ok
test_duplicate_and_overlap_rejected (test_config.ConfigTests.test_duplicate_and_overlap_rejected) ... ok
test_excluded_child_blocks_whole_target (test_config.ConfigTests.test_excluded_child_blocks_whole_target) ... ok
test_paths_outside_home_rejected (test_config.ConfigTests.test_paths_outside_home_rejected) ... ok
test_unknown_profile_fails (test_config.ConfigTests.test_unknown_profile_fails) ... ok
test_active_process_or_open_reference_blocks (test_safety.SafetyTests.test_active_process_or_open_reference_blocks) ... ok
test_ambiguous_directory_is_report_only (test_safety.SafetyTests.test_ambiguous_directory_is_report_only) ... ok
test_artifact_cleanup_unlinks_symlinks_without_following (test_safety.SafetyTests.test_artifact_cleanup_unlinks_symlinks_without_following) ... ok
test_cargo_command_pins_both_output_directories (test_safety.SafetyTests.test_cargo_command_pins_both_output_directories) ... ok
test_cargo_overrides_and_config_are_blocked (test_safety.SafetyTests.test_cargo_overrides_and_config_are_blocked) ... ok
test_desktop_removal_preserves_directory_and_outside_data (test_safety.SafetyTests.test_desktop_removal_preserves_directory_and_outside_data) ... ok
test_desktop_unknown_file_blocks_without_deletion (test_safety.SafetyTests.test_desktop_unknown_file_blocks_without_deletion) ... ok
test_gitdir_file_inside_artifact_blocks_cleanup (test_safety.SafetyTests.test_gitdir_file_inside_artifact_blocks_cleanup) ... ok
test_identity_replacement_blocked (test_safety.SafetyTests.test_identity_replacement_blocked) ... ok
test_imager_only_recognized_download (test_safety.SafetyTests.test_imager_only_recognized_download) ... ok
test_nested_git_repository_blocks_artifact_cleanup (test_safety.SafetyTests.test_nested_git_repository_blocks_artifact_cleanup) ... ok
test_node_modules_without_lockfile_is_blocked (test_safety.SafetyTests.test_node_modules_without_lockfile_is_blocked) ... ok
test_npm_only_passes_parent_cache_and_never_npx (test_safety.SafetyTests.test_npm_only_passes_parent_cache_and_never_npx) ... ok
test_npm_process_title_blocks_cleanup (test_safety.SafetyTests.test_npm_process_title_blocks_cleanup) ... ok
test_private_namespace_detected (test_safety.SafetyTests.test_private_namespace_detected) ... ok
test_process_visibility_blocks_cleanup (test_safety.SafetyTests.test_process_visibility_blocks_cleanup) ... ok
test_session_daemon_name_with_unexpected_command_blocks (test_safety.SafetyTests.test_session_daemon_name_with_unexpected_command_blocks) ... ok
test_session_daemons_hiding_descriptors_are_listed_not_blocking (test_safety.SafetyTests.test_session_daemons_hiding_descriptors_are_listed_not_blocking) ... ok
test_special_file_stops_artifact_cleanup (test_safety.SafetyTests.test_special_file_stops_artifact_cleanup) ... ok
test_symlink_added_after_preflight_is_not_followed (test_safety.SafetyTests.test_symlink_added_after_preflight_is_not_followed) ... ok
test_tracked_artifact_is_blocked (test_safety.SafetyTests.test_tracked_artifact_is_blocked) ... ok
test_tracked_cargo_file_is_blocked (test_safety.SafetyTests.test_tracked_cargo_file_is_blocked) ... ok
test_unknown_process_hiding_descriptors_blocks (test_safety.SafetyTests.test_unknown_process_hiding_descriptors_blocks) ... ok
test_unreadable_artifact_subdirectory_blocks_cleanup (test_safety.SafetyTests.test_unreadable_artifact_subdirectory_blocks_cleanup) ... ok
test_unrelated_node_process_does_not_block_artifact (test_safety.SafetyTests.test_unrelated_node_process_does_not_block_artifact) ... ok
test_uv_central_environment_blocks (test_safety.SafetyTests.test_uv_central_environment_blocks) ... ok
test_uv_environment_symlink_blocks (test_safety.SafetyTests.test_uv_environment_symlink_blocks) ... ok
test_uv_inspection_limit_is_not_success (test_safety.SafetyTests.test_uv_inspection_limit_is_not_success) ... ok
test_uv_scan_blocks_on_unreadable_directory (test_safety.SafetyTests.test_uv_scan_blocks_on_unreadable_directory) ... ok
test_uv_scan_checks_environments_inside_build_dirs (test_safety.SafetyTests.test_uv_scan_checks_environments_inside_build_dirs) ... ok
test_uv_scan_skips_trees_that_cannot_hold_environments (test_safety.SafetyTests.test_uv_scan_skips_trees_that_cannot_hold_environments) ... ok
test_windows_process_view_does_not_read_proc (test_safety.SafetyTests.test_windows_process_view_does_not_read_proc) ... ok
test_artifact_discovery_recognizes_regenerable_folders (test_scanner.ScannerTests.test_artifact_discovery_recognizes_regenerable_folders) ... ok
test_default_profile_has_eight_cargo_targets (test_scanner.ScannerTests.test_default_profile_has_eight_cargo_targets) ... ok
test_discovery_finds_worktree_but_not_dependencies (test_scanner.ScannerTests.test_discovery_finds_worktree_but_not_dependencies) ... ok
test_explicit_missing_project_root_is_reported (test_scanner.ScannerTests.test_explicit_missing_project_root_is_reported) ... ok
test_hardlinks_count_once_and_sparse_size_is_allocated (test_scanner.ScannerTests.test_hardlinks_count_once_and_sparse_size_is_allocated) ... ok
test_missing_default_root_is_not_a_scan_failure (test_scanner.ScannerTests.test_missing_default_root_is_not_a_scan_failure) ... ok
test_missing_target_is_reported (test_scanner.ScannerTests.test_missing_target_is_reported) ... ok
test_node_modules_symlinks_do_not_block_or_follow (test_scanner.ScannerTests.test_node_modules_symlinks_do_not_block_or_follow) ... ok
test_permission_failure_is_reported_as_unknown (test_scanner.ScannerTests.test_permission_failure_is_reported_as_unknown) ... ok
test_symlink_not_followed (test_scanner.ScannerTests.test_symlink_not_followed) ... ok
test_symlinked_artifact_root_is_blocked (test_scanner.ScannerTests.test_symlinked_artifact_root_is_blocked) ... ok
test_target_symlink_is_blocked (test_scanner.ScannerTests.test_target_symlink_is_blocked) ... ok
test_uv_external_links_are_blocked (test_scanner.ScannerTests.test_uv_external_links_are_blocked) ... ok
test_uv_internal_links_are_not_followed_or_double_counted (test_scanner.ScannerTests.test_uv_internal_links_are_not_followed_or_double_counted) ... ok
test_windows_measure_skips_owner_checks_and_keeps_links (test_scanner.ScannerTests.test_windows_measure_skips_owner_checks_and_keeps_links) ... ok
test_windows_measure_uses_real_stat_when_direntry_identity_is_zero (test_scanner.ScannerTests.test_windows_measure_uses_real_stat_when_direntry_identity_is_zero) ... ok
test_blocked_preflight_never_calls_execute (test_service.ServiceTests.test_blocked_preflight_never_calls_execute) ... ok
test_clean_records_verified_result_and_filesystem_once (test_service.ServiceTests.test_clean_records_verified_result_and_filesystem_once) ... ok
test_command_failure_not_reported_as_success (test_service.ServiceTests.test_command_failure_not_reported_as_success) ... ok
test_corrupt_history_blocks_cleanup (test_service.ServiceTests.test_corrupt_history_blocks_cleanup) ... ok
test_history_is_bounded (test_service.ServiceTests.test_history_is_bounded) ... ok
test_history_symlink_is_rejected (test_service.ServiceTests.test_history_symlink_is_rejected) ... ok
test_instance_lock_excludes_second_cleanup (test_service.ServiceTests.test_instance_lock_excludes_second_cleanup) ... ok
test_interrupt_saves_current_and_skips_following (test_service.ServiceTests.test_interrupt_saves_current_and_skips_following) ... ok
test_on_result_called_once_per_target_after_save (test_service.ServiceTests.test_on_result_called_once_per_target_after_save) ... ok
test_partial_removal_is_reported (test_service.ServiceTests.test_partial_removal_is_reported) ... ok
test_preflight_interrupt_is_saved_without_deletion (test_service.ServiceTests.test_preflight_interrupt_is_saved_without_deletion) ... ok
test_should_stop_skips_targets_without_deleting (test_service.ServiceTests.test_should_stop_skips_targets_without_deleting) ... ok
test_successful_noop_with_payload_is_failed (test_service.ServiceTests.test_successful_noop_with_payload_is_failed) ... ok
test_real_cargo_cleanup_preserves_source_and_lockfile (test_tool_integration.ToolIntegrationTests.test_real_cargo_cleanup_preserves_source_and_lockfile) ... skipped 'Cargo/git unavailable'
test_real_npm_cleanup_preserves_npx_and_installed_dependencies (test_tool_integration.ToolIntegrationTests.test_real_npm_cleanup_preserves_npx_and_installed_dependencies) ... ok
test_real_pip_cleanup_preserves_selfcheck (test_tool_integration.ToolIntegrationTests.test_real_pip_cleanup_preserves_selfcheck) ... ok
test_real_uv_cleanup_preserves_environment (test_tool_integration.ToolIntegrationTests.test_real_uv_cleanup_preserves_environment) ... ok
test_cli_tui_returns_cleanup_exit_code (test_tui.TuiCliTests.test_cli_tui_returns_cleanup_exit_code) ... ok
test_missing_extra_prints_install_hint (test_tui.TuiCliTests.test_missing_extra_prints_install_hint) ... ok
test_requires_terminal (test_tui.TuiCliTests.test_requires_terminal) ... ok
test_blocked_rows_cannot_be_selected (test_tui.TuiTests.test_blocked_rows_cannot_be_selected) ... ok
test_confirm_requires_exact_word_and_cancel_changes_nothing (test_tui.TuiTests.test_confirm_requires_exact_word_and_cancel_changes_nothing) ... ok
test_dry_run_preview_shows_operation_without_changes (test_tui.TuiTests.test_dry_run_preview_shows_operation_without_changes) ... ok
test_exit_code_nonzero_after_failed_skipped_or_stopped_cleanup (test_tui.TuiTests.test_exit_code_nonzero_after_failed_skipped_or_stopped_cleanup) ... Executing <Task pending name='message pump ScanScreen()' coro=<MessagePump._process_messages() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.117 seconds
ok
test_full_flow_cleans_and_records_history (test_tui.TuiTests.test_full_flow_cleans_and_records_history) ... ok
test_info_screens_open (test_tui.TuiTests.test_info_screens_open) ... Executing <Task pending name='message pump FooterKey()' coro=<MessagePump._process_messages() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.101 seconds
ok
test_insufficient_process_visibility_is_shown_and_skips (test_tui.TuiTests.test_insufficient_process_visibility_is_shown_and_skips) ... Executing <Task pending name='message pump PreviewScreen()' coro=<MessagePump._process_messages() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.117 seconds
ok
test_markup_in_paths_renders_literally (test_tui.TuiTests.test_markup_in_paths_renders_literally) ... ok
test_oserror_before_vs_after_execution_reports_accurate_status (test_tui.TuiTests.test_oserror_before_vs_after_execution_reports_accurate_status) ... Executing <Task pending name='screen_update' coro=<Timer._run_timer() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/timer.py:146> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.137 seconds
Executing <Task pending name='message pump PreviewScreen()' coro=<MessagePump._process_messages() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.104 seconds
ok
test_scan_lists_fixture_with_nothing_selected (test_tui.TuiTests.test_scan_lists_fixture_with_nothing_selected) ... Executing <Task pending name='message pump ScanScreen()' coro=<MessagePump._process_messages() running at /workspace/Workstation-Tidy/.venv/lib/python3.12/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/base_events.py:448> created at /opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/asyncio/tasks.py:420> took 0.133 seconds
ok
test_session_daemons_not_inspected_are_shown (test_tui.TuiTests.test_session_daemons_not_inspected_are_shown) ... ok

----------------------------------------------------------------------
Ran 101 tests in 19.964s

OK (skipped=1)
```

</details>

<details>
<summary>uv run ruff check .</summary>

```text
All checks passed!
```

</details>

<details>
<summary>uv run ruff format --check .</summary>

```text
24 files already formatted
```

</details>

<details>
<summary>uv run mypy src</summary>

```text
src/workstation_tidy/tui/screens.py:178: error: Signature of "visible" incompatible with supertype "DOMNode"  [override]
src/workstation_tidy/tui/screens.py:178: note:      Superclass:
src/workstation_tidy/tui/screens.py:178: note:          bool
src/workstation_tidy/tui/screens.py:178: note:      Subclass:
src/workstation_tidy/tui/screens.py:178: note:          def visible(self) -> list[Candidate]
src/workstation_tidy/tui/screens.py:306: error: Signature of "action_toggle" incompatible with supertype "textual.dom.DOMNode"  [override]
src/workstation_tidy/tui/screens.py:306: note:      Superclass:
src/workstation_tidy/tui/screens.py:306: note:          def action_toggle(self, attribute_name: str) -> Coroutine[Any, Any, None]
src/workstation_tidy/tui/screens.py:306: note:      Subclass:
src/workstation_tidy/tui/screens.py:306: note:          def action_toggle(self) -> None
Found 2 errors in 1 file (checked 14 source files)
```

</details>

<details>
<summary>uv build</summary>

```text
Building source distribution...
running egg_info
writing src/workstation_tidy.egg-info/PKG-INFO
writing dependency_links to src/workstation_tidy.egg-info/dependency_links.txt
writing entry points to src/workstation_tidy.egg-info/entry_points.txt
writing requirements to src/workstation_tidy.egg-info/requires.txt
writing top-level names to src/workstation_tidy.egg-info/top_level.txt
reading manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
reading manifest template 'MANIFEST.in'
writing manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
running sdist
running egg_info
writing src/workstation_tidy.egg-info/PKG-INFO
writing dependency_links to src/workstation_tidy.egg-info/dependency_links.txt
writing entry points to src/workstation_tidy.egg-info/entry_points.txt
writing requirements to src/workstation_tidy.egg-info/requires.txt
writing top-level names to src/workstation_tidy.egg-info/top_level.txt
reading manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
reading manifest template 'MANIFEST.in'
writing manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
running check
creating workstation_tidy-0.1.0
creating workstation_tidy-0.1.0/docs
creating workstation_tidy-0.1.0/docs/superpowers/plans
creating workstation_tidy-0.1.0/src/workstation_tidy
creating workstation_tidy-0.1.0/src/workstation_tidy.egg-info
creating workstation_tidy-0.1.0/src/workstation_tidy/tui
creating workstation_tidy-0.1.0/tests
copying files to workstation_tidy-0.1.0...
copying MANIFEST.in -> workstation_tidy-0.1.0
copying README.md -> workstation_tidy-0.1.0
copying config.example.toml -> workstation_tidy-0.1.0
copying pyproject.toml -> workstation_tidy-0.1.0
copying docs/project-status.md -> workstation_tidy-0.1.0/docs
copying docs/superpowers/plans/2026-10-21-workstation-tidy.md -> workstation_tidy-0.1.0/docs/superpowers/plans
copying src/workstation_tidy/__init__.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/__main__.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/cli.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/common.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/config.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/handlers.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/models.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/safety.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/scanner.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/service.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy/storage.py -> workstation_tidy-0.1.0/src/workstation_tidy
copying src/workstation_tidy.egg-info/PKG-INFO -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy.egg-info/SOURCES.txt -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy.egg-info/dependency_links.txt -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy.egg-info/entry_points.txt -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy.egg-info/requires.txt -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy.egg-info/top_level.txt -> workstation_tidy-0.1.0/src/workstation_tidy.egg-info
copying src/workstation_tidy/tui/__init__.py -> workstation_tidy-0.1.0/src/workstation_tidy/tui
copying src/workstation_tidy/tui/app.py -> workstation_tidy-0.1.0/src/workstation_tidy/tui
copying src/workstation_tidy/tui/screens.py -> workstation_tidy-0.1.0/src/workstation_tidy/tui
copying tests/test_cli.py -> workstation_tidy-0.1.0/tests
copying tests/test_config.py -> workstation_tidy-0.1.0/tests
copying tests/test_safety.py -> workstation_tidy-0.1.0/tests
copying tests/test_scanner.py -> workstation_tidy-0.1.0/tests
copying tests/test_service.py -> workstation_tidy-0.1.0/tests
copying tests/test_tool_integration.py -> workstation_tidy-0.1.0/tests
copying tests/test_tui.py -> workstation_tidy-0.1.0/tests
Writing workstation_tidy-0.1.0/setup.cfg
Creating tar archive
removing 'workstation_tidy-0.1.0' (and everything under it)
Building wheel from source distribution...
running egg_info
writing src/workstation_tidy.egg-info/PKG-INFO
writing dependency_links to src/workstation_tidy.egg-info/dependency_links.txt
writing entry points to src/workstation_tidy.egg-info/entry_points.txt
writing requirements to src/workstation_tidy.egg-info/requires.txt
writing top-level names to src/workstation_tidy.egg-info/top_level.txt
reading manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
reading manifest template 'MANIFEST.in'
writing manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
running bdist_wheel
running build
running build_py
creating build/lib/workstation_tidy
copying src/workstation_tidy/__main__.py -> build/lib/workstation_tidy
copying src/workstation_tidy/config.py -> build/lib/workstation_tidy
copying src/workstation_tidy/cli.py -> build/lib/workstation_tidy
copying src/workstation_tidy/storage.py -> build/lib/workstation_tidy
copying src/workstation_tidy/service.py -> build/lib/workstation_tidy
copying src/workstation_tidy/safety.py -> build/lib/workstation_tidy
copying src/workstation_tidy/__init__.py -> build/lib/workstation_tidy
copying src/workstation_tidy/models.py -> build/lib/workstation_tidy
copying src/workstation_tidy/scanner.py -> build/lib/workstation_tidy
copying src/workstation_tidy/handlers.py -> build/lib/workstation_tidy
copying src/workstation_tidy/common.py -> build/lib/workstation_tidy
creating build/lib/workstation_tidy/tui
copying src/workstation_tidy/tui/app.py -> build/lib/workstation_tidy/tui
copying src/workstation_tidy/tui/screens.py -> build/lib/workstation_tidy/tui
copying src/workstation_tidy/tui/__init__.py -> build/lib/workstation_tidy/tui
running egg_info
writing src/workstation_tidy.egg-info/PKG-INFO
writing dependency_links to src/workstation_tidy.egg-info/dependency_links.txt
writing entry points to src/workstation_tidy.egg-info/entry_points.txt
writing requirements to src/workstation_tidy.egg-info/requires.txt
writing top-level names to src/workstation_tidy.egg-info/top_level.txt
reading manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
reading manifest template 'MANIFEST.in'
writing manifest file 'src/workstation_tidy.egg-info/SOURCES.txt'
installing to build/bdist.linux-x86_64/wheel
running install
running install_lib
creating build/bdist.linux-x86_64/wheel
creating build/bdist.linux-x86_64/wheel/workstation_tidy
creating build/bdist.linux-x86_64/wheel/workstation_tidy/tui
copying build/lib/workstation_tidy/tui/app.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy/tui
copying build/lib/workstation_tidy/tui/screens.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy/tui
copying build/lib/workstation_tidy/tui/__init__.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy/tui
copying build/lib/workstation_tidy/__main__.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/config.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/cli.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/storage.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/service.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/safety.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/__init__.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/models.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/scanner.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/handlers.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
copying build/lib/workstation_tidy/common.py -> build/bdist.linux-x86_64/wheel/./workstation_tidy
running install_egg_info
Copying src/workstation_tidy.egg-info to build/bdist.linux-x86_64/wheel/./workstation_tidy-0.1.0-py3.12.egg-info
running install_scripts
creating build/bdist.linux-x86_64/wheel/workstation_tidy-0.1.0.dist-info/WHEEL
creating '/workspace/Workstation-Tidy/dist/.tmp-fxz7unx7/workstation_tidy-0.1.0-py3-none-any.whl' and adding 'build/bdist.linux-x86_64/wheel' to it
adding 'workstation_tidy/__init__.py'
adding 'workstation_tidy/__main__.py'
adding 'workstation_tidy/cli.py'
adding 'workstation_tidy/common.py'
adding 'workstation_tidy/config.py'
adding 'workstation_tidy/handlers.py'
adding 'workstation_tidy/models.py'
adding 'workstation_tidy/safety.py'
adding 'workstation_tidy/scanner.py'
adding 'workstation_tidy/service.py'
adding 'workstation_tidy/storage.py'
adding 'workstation_tidy/tui/__init__.py'
adding 'workstation_tidy/tui/app.py'
adding 'workstation_tidy/tui/screens.py'
adding 'workstation_tidy-0.1.0.dist-info/METADATA'
adding 'workstation_tidy-0.1.0.dist-info/WHEEL'
adding 'workstation_tidy-0.1.0.dist-info/entry_points.txt'
adding 'workstation_tidy-0.1.0.dist-info/top_level.txt'
adding 'workstation_tidy-0.1.0.dist-info/RECORD'
removing build/bdist.linux-x86_64/wheel
Successfully built dist/workstation_tidy-0.1.0.tar.gz
Successfully built dist/workstation_tidy-0.1.0-py3-none-any.whl
```

</details>

<details>
<summary>uv run --python 3.11 --locked --extra tui python -m unittest discover -s tests -v</summary>

```text
Downloading cpython-3.11.18-linux-x86_64-gnu (download) (29.2MiB)
 Downloaded cpython-3.11.18-linux-x86_64-gnu (download)
Using CPython 3.11.18
Creating virtual environment at: /tmp/workstation-review-py311
warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.
         If the cache and target directories are on different filesystems, hardlinking may not be supported.
         If this is intentional, set `export UV_LINK_MODE=copy` or use `--link-mode=copy` to suppress this warning.
Installed 10 packages in 69ms
test_clean_preview_names_session_daemons_not_inspected (test_cli.CliTests.test_clean_preview_names_session_daemons_not_inspected) ... ok
test_confirmation_cancel_does_not_execute (test_cli.CliTests.test_confirmation_cancel_does_not_execute) ... ok
test_confirmed_fixture_cleanup_and_history (test_cli.CliTests.test_confirmed_fixture_cleanup_and_history) ... ok
test_doctor_lists_session_daemons_not_inspected (test_cli.CliTests.test_doctor_lists_session_daemons_not_inspected) ... ok
test_dry_run_no_cleanup_or_history (test_cli.CliTests.test_dry_run_no_cleanup_or_history) ... ok
test_invalid_selection_and_sizes (test_cli.CliTests.test_invalid_selection_and_sizes) ... ok
test_json_scan_read_only (test_cli.CliTests.test_json_scan_read_only) ... ok
test_no_selection_does_not_delete (test_cli.CliTests.test_no_selection_does_not_delete) ... ok
test_noninteractive_cleanup_refused (test_cli.CliTests.test_noninteractive_cleanup_refused) ... ok
test_older_than_filters_on_newest_mtime (test_cli.CliTests.test_older_than_filters_on_newest_mtime) ... ok
test_root_refusal (test_cli.CliTests.test_root_refusal) ... ok
test_show_explains_consequences (test_cli.CliTests.test_show_explains_consequences) ... ok
test_size_and_category_filters (test_cli.CliTests.test_size_and_category_filters) ... ok
test_windows_cleanup_is_refused (test_cli.CliTests.test_windows_cleanup_is_refused) ... ok
test_windows_readonly_scan_does_not_call_geteuid (test_cli.CliTests.test_windows_readonly_scan_does_not_call_geteuid) ... ok
test_artifact_discovery_defaults_on_and_can_be_disabled (test_config.ConfigTests.test_artifact_discovery_defaults_on_and_can_be_disabled) ... ok
test_commands_not_allowed (test_config.ConfigTests.test_commands_not_allowed) ... ok
test_custom_profile_cache_and_exclusions (test_config.ConfigTests.test_custom_profile_cache_and_exclusions) ... ok
test_duplicate_and_overlap_rejected (test_config.ConfigTests.test_duplicate_and_overlap_rejected) ... ok
test_excluded_child_blocks_whole_target (test_config.ConfigTests.test_excluded_child_blocks_whole_target) ... ok
test_paths_outside_home_rejected (test_config.ConfigTests.test_paths_outside_home_rejected) ... ok
test_unknown_profile_fails (test_config.ConfigTests.test_unknown_profile_fails) ... ok
test_active_process_or_open_reference_blocks (test_safety.SafetyTests.test_active_process_or_open_reference_blocks) ... ok
test_ambiguous_directory_is_report_only (test_safety.SafetyTests.test_ambiguous_directory_is_report_only) ... ok
test_artifact_cleanup_unlinks_symlinks_without_following (test_safety.SafetyTests.test_artifact_cleanup_unlinks_symlinks_without_following) ... ok
test_cargo_command_pins_both_output_directories (test_safety.SafetyTests.test_cargo_command_pins_both_output_directories) ... ok
test_cargo_overrides_and_config_are_blocked (test_safety.SafetyTests.test_cargo_overrides_and_config_are_blocked) ... ok
test_desktop_removal_preserves_directory_and_outside_data (test_safety.SafetyTests.test_desktop_removal_preserves_directory_and_outside_data) ... ok
test_desktop_unknown_file_blocks_without_deletion (test_safety.SafetyTests.test_desktop_unknown_file_blocks_without_deletion) ... ok
test_gitdir_file_inside_artifact_blocks_cleanup (test_safety.SafetyTests.test_gitdir_file_inside_artifact_blocks_cleanup) ... ok
test_identity_replacement_blocked (test_safety.SafetyTests.test_identity_replacement_blocked) ... ok
test_imager_only_recognized_download (test_safety.SafetyTests.test_imager_only_recognized_download) ... ok
test_nested_git_repository_blocks_artifact_cleanup (test_safety.SafetyTests.test_nested_git_repository_blocks_artifact_cleanup) ... ok
test_node_modules_without_lockfile_is_blocked (test_safety.SafetyTests.test_node_modules_without_lockfile_is_blocked) ... ok
test_npm_only_passes_parent_cache_and_never_npx (test_safety.SafetyTests.test_npm_only_passes_parent_cache_and_never_npx) ... ok
test_npm_process_title_blocks_cleanup (test_safety.SafetyTests.test_npm_process_title_blocks_cleanup) ... ok
test_private_namespace_detected (test_safety.SafetyTests.test_private_namespace_detected) ... ok
test_process_visibility_blocks_cleanup (test_safety.SafetyTests.test_process_visibility_blocks_cleanup) ... ok
test_session_daemon_name_with_unexpected_command_blocks (test_safety.SafetyTests.test_session_daemon_name_with_unexpected_command_blocks) ... ok
test_session_daemons_hiding_descriptors_are_listed_not_blocking (test_safety.SafetyTests.test_session_daemons_hiding_descriptors_are_listed_not_blocking) ... ok
test_special_file_stops_artifact_cleanup (test_safety.SafetyTests.test_special_file_stops_artifact_cleanup) ... ok
test_symlink_added_after_preflight_is_not_followed (test_safety.SafetyTests.test_symlink_added_after_preflight_is_not_followed) ... ok
test_tracked_artifact_is_blocked (test_safety.SafetyTests.test_tracked_artifact_is_blocked) ... ok
test_tracked_cargo_file_is_blocked (test_safety.SafetyTests.test_tracked_cargo_file_is_blocked) ... ok
test_unknown_process_hiding_descriptors_blocks (test_safety.SafetyTests.test_unknown_process_hiding_descriptors_blocks) ... ok
test_unreadable_artifact_subdirectory_blocks_cleanup (test_safety.SafetyTests.test_unreadable_artifact_subdirectory_blocks_cleanup) ... ok
test_unrelated_node_process_does_not_block_artifact (test_safety.SafetyTests.test_unrelated_node_process_does_not_block_artifact) ... ok
test_uv_central_environment_blocks (test_safety.SafetyTests.test_uv_central_environment_blocks) ... ok
test_uv_environment_symlink_blocks (test_safety.SafetyTests.test_uv_environment_symlink_blocks) ... ok
test_uv_inspection_limit_is_not_success (test_safety.SafetyTests.test_uv_inspection_limit_is_not_success) ... ok
test_uv_scan_blocks_on_unreadable_directory (test_safety.SafetyTests.test_uv_scan_blocks_on_unreadable_directory) ... ok
test_uv_scan_checks_environments_inside_build_dirs (test_safety.SafetyTests.test_uv_scan_checks_environments_inside_build_dirs) ... ok
test_uv_scan_skips_trees_that_cannot_hold_environments (test_safety.SafetyTests.test_uv_scan_skips_trees_that_cannot_hold_environments) ... ok
test_windows_process_view_does_not_read_proc (test_safety.SafetyTests.test_windows_process_view_does_not_read_proc) ... ok
test_artifact_discovery_recognizes_regenerable_folders (test_scanner.ScannerTests.test_artifact_discovery_recognizes_regenerable_folders) ... ok
test_default_profile_has_eight_cargo_targets (test_scanner.ScannerTests.test_default_profile_has_eight_cargo_targets) ... ok
test_discovery_finds_worktree_but_not_dependencies (test_scanner.ScannerTests.test_discovery_finds_worktree_but_not_dependencies) ... ok
test_explicit_missing_project_root_is_reported (test_scanner.ScannerTests.test_explicit_missing_project_root_is_reported) ... ok
test_hardlinks_count_once_and_sparse_size_is_allocated (test_scanner.ScannerTests.test_hardlinks_count_once_and_sparse_size_is_allocated) ... ok
test_missing_default_root_is_not_a_scan_failure (test_scanner.ScannerTests.test_missing_default_root_is_not_a_scan_failure) ... ok
test_missing_target_is_reported (test_scanner.ScannerTests.test_missing_target_is_reported) ... ok
test_node_modules_symlinks_do_not_block_or_follow (test_scanner.ScannerTests.test_node_modules_symlinks_do_not_block_or_follow) ... ok
test_permission_failure_is_reported_as_unknown (test_scanner.ScannerTests.test_permission_failure_is_reported_as_unknown) ... ok
test_symlink_not_followed (test_scanner.ScannerTests.test_symlink_not_followed) ... ok
test_symlinked_artifact_root_is_blocked (test_scanner.ScannerTests.test_symlinked_artifact_root_is_blocked) ... ok
test_target_symlink_is_blocked (test_scanner.ScannerTests.test_target_symlink_is_blocked) ... ok
test_uv_external_links_are_blocked (test_scanner.ScannerTests.test_uv_external_links_are_blocked) ... ok
test_uv_internal_links_are_not_followed_or_double_counted (test_scanner.ScannerTests.test_uv_internal_links_are_not_followed_or_double_counted) ... ok
test_windows_measure_skips_owner_checks_and_keeps_links (test_scanner.ScannerTests.test_windows_measure_skips_owner_checks_and_keeps_links) ... ok
test_windows_measure_uses_real_stat_when_direntry_identity_is_zero (test_scanner.ScannerTests.test_windows_measure_uses_real_stat_when_direntry_identity_is_zero) ... ok
test_blocked_preflight_never_calls_execute (test_service.ServiceTests.test_blocked_preflight_never_calls_execute) ... ok
test_clean_records_verified_result_and_filesystem_once (test_service.ServiceTests.test_clean_records_verified_result_and_filesystem_once) ... ok
test_command_failure_not_reported_as_success (test_service.ServiceTests.test_command_failure_not_reported_as_success) ... ok
test_corrupt_history_blocks_cleanup (test_service.ServiceTests.test_corrupt_history_blocks_cleanup) ... ok
test_history_is_bounded (test_service.ServiceTests.test_history_is_bounded) ... ok
test_history_symlink_is_rejected (test_service.ServiceTests.test_history_symlink_is_rejected) ... ok
test_instance_lock_excludes_second_cleanup (test_service.ServiceTests.test_instance_lock_excludes_second_cleanup) ... ok
test_interrupt_saves_current_and_skips_following (test_service.ServiceTests.test_interrupt_saves_current_and_skips_following) ... ok
test_on_result_called_once_per_target_after_save (test_service.ServiceTests.test_on_result_called_once_per_target_after_save) ... ok
test_partial_removal_is_reported (test_service.ServiceTests.test_partial_removal_is_reported) ... ok
test_preflight_interrupt_is_saved_without_deletion (test_service.ServiceTests.test_preflight_interrupt_is_saved_without_deletion) ... ok
test_should_stop_skips_targets_without_deleting (test_service.ServiceTests.test_should_stop_skips_targets_without_deleting) ... ok
test_successful_noop_with_payload_is_failed (test_service.ServiceTests.test_successful_noop_with_payload_is_failed) ... ok
test_real_cargo_cleanup_preserves_source_and_lockfile (test_tool_integration.ToolIntegrationTests.test_real_cargo_cleanup_preserves_source_and_lockfile) ... skipped 'Cargo/git unavailable'
test_real_npm_cleanup_preserves_npx_and_installed_dependencies (test_tool_integration.ToolIntegrationTests.test_real_npm_cleanup_preserves_npx_and_installed_dependencies) ... ok
test_real_pip_cleanup_preserves_selfcheck (test_tool_integration.ToolIntegrationTests.test_real_pip_cleanup_preserves_selfcheck) ... ok
test_real_uv_cleanup_preserves_environment (test_tool_integration.ToolIntegrationTests.test_real_uv_cleanup_preserves_environment) ... ok
test_cli_tui_returns_cleanup_exit_code (test_tui.TuiCliTests.test_cli_tui_returns_cleanup_exit_code) ... ok
test_missing_extra_prints_install_hint (test_tui.TuiCliTests.test_missing_extra_prints_install_hint) ... ok
test_requires_terminal (test_tui.TuiCliTests.test_requires_terminal) ... ok
test_blocked_rows_cannot_be_selected (test_tui.TuiTests.test_blocked_rows_cannot_be_selected) ... ok
test_confirm_requires_exact_word_and_cancel_changes_nothing (test_tui.TuiTests.test_confirm_requires_exact_word_and_cancel_changes_nothing) ... ok
test_dry_run_preview_shows_operation_without_changes (test_tui.TuiTests.test_dry_run_preview_shows_operation_without_changes) ... ok
test_exit_code_nonzero_after_failed_skipped_or_stopped_cleanup (test_tui.TuiTests.test_exit_code_nonzero_after_failed_skipped_or_stopped_cleanup) ... Executing <Task pending name='screen_update' coro=<Timer._run_timer() running at /tmp/workstation-review-py311/lib/python3.11/site-packages/textual/timer.py:146> wait_for=<Future pending cb=[Task.task_wakeup()] created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/base_events.py:428> created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/tasks.py:384> took 0.101 seconds
ok
test_full_flow_cleans_and_records_history (test_tui.TuiTests.test_full_flow_cleans_and_records_history) ... ok
test_info_screens_open (test_tui.TuiTests.test_info_screens_open) ... Executing <Task pending name='message pump FooterKey()' coro=<MessagePump._process_messages() running at /tmp/workstation-review-py311/lib/python3.11/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/base_events.py:428> created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/tasks.py:384> took 0.100 seconds
ok
test_insufficient_process_visibility_is_shown_and_skips (test_tui.TuiTests.test_insufficient_process_visibility_is_shown_and_skips) ... ok
test_markup_in_paths_renders_literally (test_tui.TuiTests.test_markup_in_paths_renders_literally) ... ok
test_oserror_before_vs_after_execution_reports_accurate_status (test_tui.TuiTests.test_oserror_before_vs_after_execution_reports_accurate_status) ... Executing <Task pending name='message pump FooterKey()' coro=<MessagePump._process_messages() running at /tmp/workstation-review-py311/lib/python3.11/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/base_events.py:428> created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/tasks.py:384> took 0.109 seconds
ok
test_scan_lists_fixture_with_nothing_selected (test_tui.TuiTests.test_scan_lists_fixture_with_nothing_selected) ... ok
test_session_daemons_not_inspected_are_shown (test_tui.TuiTests.test_session_daemons_not_inspected_are_shown) ... Executing <Task pending name='message pump InfoScreen()' coro=<MessagePump._process_messages() running at /tmp/workstation-review-py311/lib/python3.11/site-packages/textual/message_pump.py:571> wait_for=<Future pending cb=[Task.task_wakeup()] created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/base_events.py:428> created at /workspace/.tools/python/cpython-3.11.18-linux-x86_64-gnu/lib/python3.11/asyncio/tasks.py:384> took 0.115 seconds
ok

----------------------------------------------------------------------
Ran 101 tests in 20.707s

OK (skipped=1)
```

</details>
