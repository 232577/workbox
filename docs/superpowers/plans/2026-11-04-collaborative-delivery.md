# Collaborative workbench delivery Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Preserve source, artifact, online and installed evidence separately.

**Goal:** Deliver B's reviewed #30/#31/#32 changes together as a new, verifiable build on the connected computer.
**Architecture:** Preserve the reviewed desktop implementation at main 19c3cdf2cce10ff8e582e2e32d86c75422acd396; add only a distinguishable build identity. Build from reviewed main, verify anonymous frozen behavior, publish immutable artifacts, synchronize the website through a separate PR, and use the installed updater after a recovery point.
**Tech Stack:** Existing pinned Python 3.11, PySide6/QFluentWidgets, SQLite, PyInstaller and Inno Setup; existing Cloudflare site.
**Spec:** AGENTS.md, docs/RELEASING.md, Issue #7 and PRs #30, #31, #32; the maintainer's continuing UI, functional and online-update request.

## Global Constraints

- Software version stays 1.0.0. Candidate v1.0.0.20261104.1 must be newer than published v1.0.0.20261103.5. Never overwrite an old tag, release asset or another developer's branch.
- Four project states, immutable delivery history, source files, registered directories and explicit-save behavior remain unchanged.
- Only the floating orb stays topmost; the expanded manager remains a normal window.
- Anonymous fixtures only for testing. Production data is read only for authorized recovery/preservation checks, never used as test fixtures or uploaded.
- No automatic claim of second-device installation or independent-account approval. No claim that a passing unit test proves the installed binary.

## Review Focus

Stale stable.json masking a new release; date entry clamping or discarded edits; compact-window clipping; lost split/selection state; interrupted updater handoff or mismatched binary/data directory. Preserve failures, never lower assertions to publish.

## Tasks

- [ ] Inspect exact source, latest collaboration notes and original installation identity; record A's integration ownership in #7 without assigning unconfirmed work to B/C.
- [ ] Add a failing build-order regression, bump only the build tag, run relevant and complete Windows/website checks in a fresh locked environment; normal PR and required checks before integration.
- [ ] Freeze reviewed main, compare embedded source and complete payload hashes; install into anonymous target, inspect actual workbench/date/delivery/settings UI in both themes, run the merged release consistency CLI.
- [ ] Create a new release draft, verify every asset, publish; update and test website catalog through a separate PR preserving old downloads and screenshot provenance.
- [ ] Deploy exact reviewed site, verify manifest and complete downloads; create an independent recovery point, upgrade through the installed app, verify new identity/UI and preserved business records/files.
- [ ] Record results and precise remaining boundaries in #7; end only this task's workflow, leaving the user's installed application running independently.
