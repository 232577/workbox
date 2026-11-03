# Secondary UI Completion Implementation Plan

> Execute with superpowers:executing-plans, in the isolated feat/ui-completion clone.

**Goal:** Make the desktop's settings and secondary workflows consistently navigable, readable and safe.
**Architecture:** Small dialog_chrome module reuses DialogHeader/StatusBanner; existing Qt controls and services remain. Local search filters existing widgets, not business data.
**Tech Stack:** Python 3.11 / pinned PySide6 and QFluentWidgets.
**Spec:** docs/UI-COMPLETION.md.

## Constraints and review focus
Keep software 1.0.0, existing explicit-save/immediate-type-save boundaries, native focus/scroll/default-cancel behavior and normal main-window stacking. No database migrations, credentials, real data fixtures, new dependencies or parallel release writes.
Review long names, no search matches, Enter in search, hidden selected type, errors at minimum window size, and deferred callbacks after deletion.

- [x] Task 1: tests/test_ui_completion.py contracts fail on absent shared header/footer/error behavior. Implement dialog_chrome.py and narrowly scoped theme rules; adapt the ten listed dialogs without changing services. Run the affected tests.
- [x] Task 2: tests fail for settings search/section/pending/Enter and type filtering. Implement non-mutating filters with no-results states. Keep original controls and schema/transactions. Run all settings, type and lifecycle tests.
- [ ] Task 3: actual anonymous Qt capture in both themes and minimum sizes; inspect pixels, focused/full/site/safety tests with unchanged source. Review exact diff and open a normal PR; fresh CI, then normal merge. Record accurate source/installation boundaries in #17.

Runtime and publication remain serialized with the current release owner; do not steal its workflow or modify its uncommitted files.
