# Online updates Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Add verified, user-confirmed online upgrade and deploy a verified build without touching existing business files.
**Architecture:** Standard-library HTTPS client, Qt background dialog, existing controller shutdown and database lock, native Windows installer handoff. One independent feature branch; B retains PR #8.
**Tech Stack:** Python3.11 / PySide6 / SQLite / Inno Setup6 / GitHub Releases.
**Spec:** `docs/ONLINE-UPDATES.md`, Issue #9.

## Constraints and review focus

Keep1.0.0/four states/data intact. Require official HTTPS source, bounded input, exact artifact identity, cancellation cleanup, safe shutdown and backup before install. Never disable Git rules or overwrite release assets. Same-account review is not independent-account approval.

- [ ] Task1: add red parser/download/backup/installer-handoff tests in `tests/test_online_updates.py`; implement `update_client.py` and `update_install.py`; run focused suite.
- [ ] Task2: red UI/controller lifecycle tests; implement `update_ui.py`, entry signal, shutdown backup and deferred install integration; exercise offline/cancel/error/same-version states and native UI.
- [ ] Task3: full local and cloud checks; PR review/integration. Build exact integrated source, verify frozen runtime and new installer, then publish new immutable-by-policy tag/assets.
- [ ] Task4: create controlled recovery point using retained private upgrade tool, install on confirmed running device, check files/data/runtime and record remaining limits. Old releases retained; no second-device claim without B evidence.
