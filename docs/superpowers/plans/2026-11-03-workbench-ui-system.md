# Workbench UI System Implementation Plan

> Execute with superpowers:executing-plans, task-by-task; preserve the active release checkout.

**Goal:** Improve WorkBox's shared visual system, material workflow and update feedback, then attempt a verified online upgrade.
**Architecture:** Extend existing Qt theme and shared components, retain business services and update protocol. Add view-only material filtering/summary, no schema migration.
**Tech Stack:** Python 3.11, PySide6 6.8.3, pinned QFluentWidgets, SQLite, PyInstaller/Inno Setup.
**Spec:** docs/UI-DESIGN-SYSTEM.md.

## Constraints and review focus

Version remains 1.0.0. Preserve current files, four project states, delivery/workload, explicit rule saves, native menus/scrolling and shutdown. No credentials, real data or private screenshots in Git. No changes to another active worktree. No old CI/native evidence reused for changed source.

Review long Unicode titles and inline editor clipping; theme switching with selected rows; incomplete and stale material results; busy dialog cancellation; downloaded-but-not-verified installer state. Every one needs targeted regression or explicit recorded native acceptance.

- [x] Capture anonymous current windows and run baseline UI tests.
- [x] Add tests/test_ui_design_system.py for semantic tokens/shared status/header/metric components, material action discovery, filter/summary/no side effects and update phases. Observe missing-feature failures.
- [x] Extend theme.py and ui_components.py; integrate compact_ui.py, material_check_ui.py and update_ui.py. New public helpers must remain presentation-only.
- [x] Review and integrate PR #18 column-width work on exact published SHA, without editing its branch. Run affected tests and native screenshot checks.
- [ ] Run complete Windows/site/safety checks; commit explicit files, push task branch, open PR, verify current CI/review and integrate normally.
- [ ] Inspect installed build and release prerequisites. Build only reviewed main, validate isolated frozen install and data preservation, use a new immutable release identity, then attempt online update. Stop and report any unmet gate truthfully; do not reuse other agent's uncommitted release tree.

Execution record: baseline43 passed, six missing-feature regressions failed before implementation. Integrated source f04783d passed1644 complete Windows cases with2 explicit UNC skips and61 website cases. Real native captures cover light/dark and supported compact sizes. Follow-up a11y review added a failing dynamic-button-name regression and removed the fixed shared name; final evidence belongs to the subsequent exact commit/CI, not the earlier SHA. Actual installed build v1.0.0.20261102.1 was checked through its UI: primary update metadata404, backup GitHub API403 rate limit; no download or installation occurred. Publishing/install acceptance remain blocked and unchecked above.
