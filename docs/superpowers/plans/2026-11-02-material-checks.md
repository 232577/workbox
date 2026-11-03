# Material checks Implementation Plan

> Execute with superpowers:executing-plans; task #15, independent feature branch.

**Goal:** Adapt useful BOX 54 matching and completeness checks into the public project without changing delivery or files.
**Architecture:** Pure standard-library engine; read-only adapter of existing project document metadata; modal Qt rule/check view; a small lazy project context-menu entry.
**Tech Stack:** Python 3.11, PySide6 6.8.3, existing SQLite settings and FileJob.
**Spec:** docs/MATERIAL-CHECKS.md.

## Constraints and review focus

No new dependencies, updater/installer changes, schema migrations, real data tests or wholesale fork import. Preserve 1.0.0 and immutable delivery records. Limit 40 rules, 20 copies per rule, 12 keywords per rule and 10,000 input file records. Empty/partial/error/ambiguous results must not claim complete. No uncontrolled scans; use registered-project query and cancel its worker before disposal. Validate saved settings before applying. Native screenshots are evidence of actual Qt UI, not generated designs.

- [ ] Task 1: failing tests in tests/test_material_matching.py; implement immutable Requirement/MaterialFile and match_materials plus explicit-choice and document snapshot adapters. Read no disk or database in the engine. Run focused tests.
- [ ] Task 2: failing tests in tests/test_material_check_ui.py; implement material_check_ui.py, explicit per-project rule persistence, bounded background refresh, visible errors, ambiguity selection and compact_ui.py lazy menu entry. No automatic saved-rule mutation.

Interface ruling: DesktopService.set_setting intentionally accepts only geometry/theme. Add material_check_store.py for the validated per-project namespace in existing desktop_settings, without widening the service setter or migrating schemas. desktop_controller.shutdown calls the new modal's shutdown before disposal/backup; this small lifecycle hook is necessary to preserve worker safety and is recorded in #15.
- [ ] Task 3: native anonymous Qt inspection, full test collection/site/safety checks; review exact diff, submit normal PR, verify all required CI, merge only tested up-to-date head under existing protection. Record exact main SHA and avoid claiming installation.

Ruling: follow the existing staged migration discussion and the latest explicit instruction to implement and integrate, without a fresh product rewrite. The first usable increment is checking, not copying or directory migration.
