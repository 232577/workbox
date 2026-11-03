# Update release consistency gate Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Run each failing test before implementation.

**Goal:** Detect mismatches between a proposed stable update, installer bytes, build report, runtime evidence and payload before release.
**Architecture:** An offline, read-only CLI added alongside existing build tools. Reuse established tag and path validation without changing the updater, installer, website or active developer branches.
**Tech Stack:** Python 3.11 standard library, existing WorkBox utilities, pytest.
**Spec:** `docs/RELEASING.md`, `docs/ONLINE-UPDATES.md`, Issue #9.

## Global constraints

- Keep software version 1.0.0 and immutable release assets. No network, installer execution, data access, or automatic publishing.
- Work in an independent clone on `chore/update-release-gate`; leave the active implementation checkout alone.
- All inputs are explicitly supplied evidence. Consistent reports do not prove an installer contains the payload, source provenance, an independent signature, successful installation, or second-device acceptance.
- Preserve failed evidence, do not lower assertions, and do not publish private local paths.

## Review focus

Wrong date/revision, stale executable evidence, manipulated or missing payload, Windows path aliases, duplicate JSON keys, and success-looking reports on errors must fail closed.

## Task 1: Offline consistency gate

Files: new `scripts/verify_update_release.py`, `tests/test_update_release_gate.py`.
Interface: `verify_release(manifest, build_info, runtime_info, installer, payload, expected_tag) -> dict`; exceptions derive from ValueError. CLI accepts five input paths/values and prints one JSON result with nonzero exit on failure.

- [ ] Write isolated positive and negative fixtures; observe failures for the missing gate.
- [ ] Validate release identity, actual installer hash/length/MZ, build/runtime evidence and exact payload inventory/hashes, rejecting links and unsafe manifest entries.
- [ ] Add bounded strict-JSON CLI input, explicit failure output and read-only tests.
- [ ] Verify targeted and full Windows tests with unchanged tested sources.

## Task 2: Handoff

Files: new `docs/UPDATE-RELEASE-GATE.md`.

- [ ] Document exact input preparation, limitations and exit codes.
- [ ] Review only the new files, scan the staged tree, push the isolated branch and create a PR under #9.
- [ ] Report actual results to the integrating developer. Do not merge or deploy concurrently with the existing execution workflow.
