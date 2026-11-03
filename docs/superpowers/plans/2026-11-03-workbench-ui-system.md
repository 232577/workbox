# Workbench UI System Implementation Plan

> Execute with superpowers:executing-plans, task-by-task; preserve the active release checkout.

**Goal:** Improve WorkBox's shared visual system, material workflow and update feedback, then attempt a verified online upgrade.
**Architecture:** Extend existing Qt theme and shared components, retain business services and update protocol. Add view-only material filtering/summary, no schema migration.
**Tech Stack:** Python 3.11, PySide6 6.8.3, pinned QFluentWidgets, SQLite, PyInstaller/Inno Setup.
**Spec:** docs/UI-DESIGN-SYSTEM.md.

## Constraints and review focus

Version remains 1.0.0. Preserve current files, four project states, delivery/workload, explicit rule saves, native menus/scrolling and shutdown. No credentials, real data or private screenshots in Git. No changes to another active worktree. No old CI/native evidence reused for changed source.

Review long Unicode titles and inline editor clipping; theme switching with selected rows; incomplete and stale material results; busy dialog cancellation; downloaded-but-not-verified installer state. Every one needs targeted regression or explicit recorded native acceptance.

- [ ] Capture anonymous current windows and run baseline UI tests.
- [ ] Add tests/test_ui_design_system.py for semantic tokens/shared status/header/metric components, material action discovery, filter/summary/no side effects and update phases. Observe missing-feature failures.
- [ ] Extend theme.py and ui_components.py; integrate compact_ui.py, material_check_ui.py and update_ui.py. New public helpers must remain presentation-only.
- [ ] Review and integrate PR #18 column-width work on exact published SHA, without editing its branch. Run affected tests and native screenshot checks.
- [ ] Run complete Windows/site/safety checks; commit explicit files, push task branch, open PR, verify current CI/review and integrate normally.
- [ ] Inspect installed build and release prerequisites. Build only reviewed main, validate isolated frozen install and data preservation, use a new immutable release identity, then attempt online update. Stop and report any unmet gate truthfully; do not reuse other agent's uncommitted release tree.
