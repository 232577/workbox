# Cube Brand Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task by task. Checkboxes record verified progress.

**Goal:** Integrate the approved cube/W logo and compact floating entry into the latest WorkBox source.

**Architecture:** Keep `branding.py` as the only geometry source. Keep all floating-size preferences local, validated without importing Qt into the service layer. The existing controller owns persistence and screen clamping.

**Tech Stack:** Existing Python / PySide6 / QtSvg / SQLite; no new dependencies.

**Spec:** `docs/UI-CUBE-BRAND.md`

## Global Constraints

Preserve native interactions, user data, Studio layout, version 1.0.0 and published releases. Work from fetched `origin/main` on a feature branch. Source edits use patches; build output stays local.

## Review Focus

- Small-size and high-DPI marks must stay complete and legible.
- Invalid saved sizes must fall back without rewriting preferences.
- Failed preference persistence must not resize the UI.
- Tray and floating menus must reflect the current size when reopened.
- Growing at the display edge must not place the entry outside its available geometry.

## Task 1: Brand and floating presentation

**Files:** `branding.py`, `assets/workbox.svg`, `website/public/favicon.svg`, `floating.py`, `floating_settings.py`, `studio_design.py`, `tests/test_branding.py`, `tests/test_cube_brand.py`, `tests/test_floating.py`.

**Interfaces:** Preserve `svg_bytes(background=True)`, `draw_mark`, `brand_icon`, `write_ico`. Add `FLOATING_SIZES`, `DEFAULT_FLOATING_SIZE`, `validate_floating_size`; `FloatingBall.set_orb_size(size)` and `size_requested(int)`.

- [x] Add and observe failing tests for the cube/white-W palette, default size, supported sizes, state isolation and input behavior.
- [x] Implement the vector, size-scaled painting and native size submenu; regenerate exact SVG copies.
- [x] Run branding, floating, theme and geometry regressions.

## Task 2: Persist and verify the entry

**Files:** `desktop_service.py`, `desktop_controller.py`, `tests/test_cube_brand.py`, `scripts/capture_cube_brand.py`, `DESIGN.md`, `docs/UI-CUBE-BRAND.md`.

**Interfaces:** `desktop_settings.orb_size`; controller `change_orb_size(size)` persists before applying and uses existing screen clamping.

- [x] Observe failing size validation, restore, persistence-failure and display-edge tests.
- [x] Wire the preference through the controller without changing workflow policy.
- [x] Capture real Qt evidence at native and high DPI using isolated data; inspect the images.
- [x] Run affected tests, complete desktop collection, website tests and repository guard; record exact outcomes, then explicitly stage and commit only task files.

## Alignment and review additions

The latest-base check also found an outdated local main reference. Fast-forward it without changing the active feature work; document the source/branch/build/runtime distinction in CONTRIBUTING.md. Do not equate source completion with an installed update.

Review found stale favicon cache keys across all four website pages. Add `website/cube-brand.test.mjs`, observe the old-key failures, then update only the icon URLs. Preserve release catalogs, historical screenshots and analytics permissions.

## Verification outcome

Full desktop collection: 189 test files, 2874 passed, 2 real-UNC environment skips, no failures or errors. Targeted suite: 69 passed. Native Windows brand suite: 51 passed. Website: 145 passed. Repository guard and diff checks passed. Exact warnings, interrupted-run handling, actual anonymous screenshots and limits are recorded in `docs/UI-CUBE-BRAND.md`.

The feature is source-only until its PR and a separate verified release are complete. The installed runtime, business data and website deployment were not replaced by this task.
