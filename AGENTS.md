# WorkBox product repository

This repository is the canonical shared source for WorkBox. Every machine and agent must start from this Git history, not from a private directory export. Private historical development repositories are not upstreams and must never be pushed here.

## Collaboration

Run `python scripts/dev.py doctor`, inspect current status and fetch before work. Use `python scripts/dev.py start feat/task-name` from a clean checkout; each concurrent task uses its own clone or worktree. Do not alter another task's files or switch its active checkout. Use explicit file staging, meaningful commits, a feature-branch push and a pull request. Read CI results before merging. Never force-push main, rewrite release tags, auto-stash, reset, or overwrite conflicts. An unfinished task must leave its branch, commit, test evidence and remaining work recorded.

Run `python scripts/dev.py setup` once per clone. Hooks are local guardrails; server-side protection and CI are authoritative. Do not bypass them merely to publish faster. See CONTRIBUTING.md and docs/RELEASING.md.

## Product constraints

Stack: Python, PySide6, SQLite and PyInstaller; software version remains 1.0.0. Four project states: 待开发、开发中、待交付、已交付. Urgency, project type and workload eligibility are independent. Never add accounts, cloud data sync, clipboard monitoring, approval workflows or background message reading without a new request.

Intake copies and verifies files; originals and archive basenames remain intact. ZIPs are not extracted or executed. Ambiguous matching needs explicit selection. Immutable delivery events and append-only corrections must be preserved. First valid delivery counts by project and Asia/Shanghai day; re-delivery is history, not another new project. Revoking an effective event returns the project to 待交付; restoring validity alone does not set 已交付. Excluding personal workload is not revoking delivery.

For ordinary document intake, a different revision at the same managed destination keeps the incoming standard filename; rename the previous working copy to a non-overwriting version name and preserve its identity/history. Keep external sources and ZIP/archive names unchanged. Rotation requires identity checks, transaction-bound recovery records, and safe restart recovery; unresolved recovery blocks storage mutations. Never silently suffix the new ordinary document to bypass an unsafe collision.

Delivery-table export is read-only and uses one database snapshot. Export one row per canonical project with effective delivery history, including workload-excluded deliveries; do not infer zero from missing archive-count facts. Export never applies project types or changes delivery validity. Recheck current managed-path boundaries before publishing the output file.

Rename/merge needs an explicit current preview and separate confirmation. Preserve original materials and historical delivery identities; do not merge automatically at startup. New default type never backfills old projects. Archive/deletion are reversible project visibility actions, not file deletion.

Use mature Qt controls, native menus and scrolling. Keep QFluentWidgets pinned and preserve selection, scroll and worker shutdown safety. Test Windows through `python -m scripts.run_tests`, not by disabling system-wide crash reporting. Fixtures must be isolated and anonymous. Do not open or modify a person's actual database for tests.

## Change and release discipline

Use apply_patch for source changes. Add and run failing tests, implement the smallest change, then run the affected and full suites. Do not relabel a failed run as passed or silently lower assertions. Record environment skips and warnings. UI changes require actual browser/Qt screenshots, not generated pictures presented as software.

Never commit data, archives, backups, credentials, private screenshots, local absolute paths or installer binaries. Website illustrative demos use fixed examples and must say they do not process real files. Desktop data is not synchronized by Git. Keep download checksums, old release routes, media provenance, site statistics permissions and disabled sponsorship intact.

Release only from reviewed commits on main. Tags and already-published assets are immutable. Installers, source snapshots and website deployments must have explicit source commits and SHA-256 evidence. Test status does not prove a second physical device was tested. Keep local OAuth credentials out of CI and never expose secrets to pull-request code.
