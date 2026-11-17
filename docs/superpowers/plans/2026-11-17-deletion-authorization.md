# Deletion Authorization Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close REVIEW.md Batch 1 (A1/A2), preventing inherited Git settings and nested repositories from authorizing deletion of protected files.

**Architecture:** Share a sanitized Git inspection path between Cargo and artifact checks. Verify the inspected worktree contains the project and reuse nested-repository traversal for both handlers. Keep revalidation at execution time.

**Tech Stack:** Python 3.11+, stdlib, unittest, native Git and Cargo fixture integration.

**Spec:** REVIEW.md, findings A1/A2 and suggested Batch 1; docs/recommendations.md items 1–2.

## Global Constraints

- Use the existing isolated cloud checkout on a dedicated branch; no extra worktree.
- Preserve read-only commands, user confirmation, fail-closed process checks and the stdlib-only CLI runtime.
- Never clean real user data; experiments use disposable HOME and XDG directories.
- No unrelated TUI, state-schema, parser or scan-budget fixes in this batch.

## Review Focus

- Alternate index, repository, worktree and command-line configuration injection must not conceal tracked output.
- Worktrees and projects nested below the repository root must remain usable.
- Git failures and mismatched/noncanonical returned repository roots must fail closed.
- Nested .git directories, .git files and recognizable bare repositories must be preserved.
- Unreadable descendants and nested repositories introduced after preview must still block execution.

### Task 1: Git authorization (A1)

**Files:** src/workstation_tidy/handlers.py; tests/test_git_authorization.py; existing tracked-file mocks in tests/test_safety.py.

**Interfaces:** Handler.check_untracked(project: Path, folder: str) -> None is shared by artifact and Cargo handlers. Git subprocesses receive an explicit environment without GIT_* overrides, and with system/global configuration disabled. A successful rev-parse result must identify a canonical ancestor worktree of project.

- [x] Add real-Git tests for alternate index/repository/worktree and GIT_CONFIG_COUNT/GIT_CONFIG_PARAMETERS; test both handlers, positive ordinary/nested projects and linked worktrees, command failure and unrelated root results.
- [x] Run the new suite and observe failures against the old implementation.
- [x] Implement sanitized Git inspection and common worktree/tracked-file validation. Update existing mocks to return valid fixture roots.
- [x] Run focused regressions and existing safety tests; commit the fix with tests.

### Task 2: Nested repositories (A2)

**Files:** src/workstation_tidy/handlers.py; tests/test_git_authorization.py; tests/test_tool_integration.py.

**Interfaces:** Existing Handler.find_nested_git(folder: Path) -> Path | None is the common fail-closed traversal. It also recognizes a bare repository's HEAD/objects/refs layout conservatively.

- [ ] Add Cargo negative tests for committed nested Git, .git files, bare repositories, unreadable paths and insertion after preflight; assert protected source/history remains. Add positive Cargo fixtures so safety checks are not merely always rejecting.
- [ ] Run and observe the missing protection before implementing it.
- [ ] Reuse the nested traversal in Cargo checks and add bare-repository recognition, without following symlinks.
- [ ] Run all tests, including actual Cargo fixture cleanup in a locally installed toolchain if unavailable in the base image. Run Python 3.11, Ruff, mypy and package build; distinguish pre-existing C1 type errors.
- [ ] Update recommendations/status with completed A1/A2 evidence and remaining C1/D1/C2/F1 items; commit, review the complete diff, push and open a PR against main.

## Execution record

The user requested implementation and a PR for the next open batch; the committed review and recommendations supply its scope and approach. Implementation runs inline in the existing managed checkout. Tests and outputs are recorded under /tmp; the test-driven-development skill package is unavailable, so explicit failing-then-passing regressions enforce that workflow directly.

Task 1 completed: six real-Git tests (including override subcases) and 32 existing safety tests pass. The pre-fix regression run had six authorization/root-check failures.
