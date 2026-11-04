# Text intake identity and release plan

> Execute inline with superpowers:executing-plans; preserve red/green evidence.

**Goal:** Prevent quick text intake from silently duplicating an existing project, and deliver the reviewed fixes with the completed workbench.

**Architecture:** Resolve normalized complete project names inside the existing SQLite write transaction. Reuse one eligible canonical project without resetting its status, batch, type, files or delivery history. Only create names with no existing identity. Ambiguous or protected names keep the whole receipt pending for explicit resolution. The explicit legacy batch-creation API remains available for separately specified batches; it is not the quick intake path.

**Tech stack:** Existing Python, SQLite, PySide6 and locked build tools; no new dependencies.

**Spec:** AGENTS.md, docs/RELEASING.md and the owner's report of duplicate names and direction-dependent rename/merge.

## Constraints and findings

- Production diagnosis is read-only, restricted to the explicitly named case; its name, documents, database and paths never enter public tests or commits.
- The observed case was file intake followed by a separate text batch, then an already completed user-confirmed merge. Do not undo, replay or re-merge those real records.
- Two anonymous merge directions currently pass. A historical refusal has no retained preview-error log, so do not invent a status-based root cause or remove directory/lock/archive protections.
- Do not move existing project directories to the new receipt's batch or automatically merge existing duplicates.
- Keep originals, archive identities, only-orb topmost and explicit merge confirmation.

## Review focus

Normalized names, already merged aliases, concurrent/pending review, archived/deleted names, and a mixed batch whose final item is ambiguous must not create duplicates or partial projects.

## Tasks

- [ ] Add failing file-first/text-first/review-confirmation/name-boundary regressions; both merge directions remain independent positive controls.
- [ ] Resolve quick text identities transactionally and explain reuse or pending conflicts in receipt feedback.
- [ ] Add bounded local-only preview diagnostics for blocked merge reasons without changing business outcomes or weakening safety checks.
- [ ] Verify affected and full Windows suites, exact candidate UI, frozen modules and installer; review and merge normally.
- [ ] Use a distinct .20261104.2 build; publish immutable assets and update the stable catalog only after validation.
- [ ] Create and verify a recovery point, perform the installed client's online upgrade, check build/data/runtime, and record exact completion boundaries.
