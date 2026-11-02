# BOX54 Independent Acceptance Implementation Plan

> For agentic workers: use superpowers:executing-plans. Never modify the other material implementation worktree.

**Goal:** Verify the selected BOX54 material-check behaviors before they are treated as integrated main functionality.
**Architecture:** Separate test branch, anonymous metadata only, public engine contract from #15; implementation remains owned by its existing author.
**Tech Stack:** Python 3.11, pytest, existing WorkBox test runner.
**Spec:** `docs/BOX54-MIGRATION-ACCEPTANCE.md` and Issue #15.

## Constraints and review focus

No live data, filesystem scans, deployment, dependency changes, or product-state writes. Cover malformed metadata, misleading filenames, ambiguity, partial scans and immutable inputs. Do not normalize away a meaningful filename boundary or claim content validation from suffix checks.

## Task 1: independent acceptance tests

- [ ] Add `tests/test_box54_material_acceptance.py` for `Requirement`, `MaterialFile`, `match_materials`, `check_snapshot`, `encode_rules` and `decode_rules` as specified by #15.
- [ ] Run against current main before the feature exists; record the missing-engine failures.
- [ ] Fetch an explicit published implementation SHA, preserving this branch and all other worktrees. Run these tests and report actual compatibility failures to the implementation author, without weakening assertions to conceal defects.
- [ ] Run the integrated source's affected tests, full Windows suite, website and repository safety checks; record skips separately.
- [ ] Commit only the named test and documentation files. Submit a PR with exact dependency/target SHA and truthful validation evidence; merge only via existing protection once the dependency is in main and current checks pass.

## Task 2: source review and handoff

- [ ] Report which BOX54 behaviors were retained, intentionally changed or deferred.
- [ ] Provide reproducible review findings or pass evidence on the exact published commit.
- [ ] Re-read remote state before the final report. A pending PR is not merged functionality; merged source is not an installed binary.
