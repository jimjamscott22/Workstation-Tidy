# Workstation Tidy Implementation Plan

**Goal:** Implement the approved Python CLI scan → select → preview → confirm → clean → verify design.

**Architecture:** Standard-library CLI with typed candidates, bounded scans, built-in cleanup handlers, shared safety checks, and JSON history. All destructive tests use temporary fixtures or mock commands. Native execution in this session; no delegation required.

**Tech stack:** Python 3.11+, argparse, dataclasses, tomllib, unittest; uv for development and packaging.

**Spec:** The approved conversation design: user-level developer and desktop cleanup, numbered interactive selection, no preselection, no unattended deletion, no privileged cleanup, eight historical Cargo targets plus configurable roots, package caches, thumbnails and recognized Pi Imager downloads. Unknown application data remains report-only.

## Global constraints

- No actual user-cache cleanup during implementation or verification.
- No shell commands from config; no automatic privilege escalation or recursive-deletion fallback.
- Exact selected paths only; preserve source, settings, installed environments and Local History.

## Review focus

Path replacement, sparse/hardlinked storage, hidden process namespaces, cleanup tools with broader configured directories, and interruption/history failures must not become false successes.

## Tasks

- [x] Configuration and models: validate TOML profiles and exclusions, seed usual candidates, define typed measurements/results. Test invalid settings, duplicate/overlapping paths and profile selection.
- [x] Scanner and safeguards: no-follow same-filesystem sizing, metadata-only discovery, path identity, active-process detection, cache links, generated-output checks. Test symlinks, permissions, changed identities and blocked state.
- [x] Cleanup handlers: explicit npm/uv/pip commands, constrained Cargo clean and narrow descriptor-based desktop deletion. Test exact arguments, failures, partial results and protected data.
- [x] CLI and persistence: scan/show/clean/doctor/history/config; JSON, filters, preview, explicit confirmation, single-instance lock, bounded atomic history. Test cancellation, dry-run, non-TTY refusal and interruption.
- [x] Verify and document: run fixture suite, package/build checks and read-only local scans; document commands, config and verification boundaries; update status and commit locally.
