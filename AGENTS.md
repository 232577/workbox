# WorkBox product repository

This repository is the canonical shared source for WorkBox. Every machine and agent must start from this Git history, not from a private directory export. Private historical development repositories are not upstreams and must never be pushed here.

## Collaboration

For this project, device A coordinates integration and releases; device B collaborates and independently verifies handoffs. Before editing, inspect Concord work and claim a stable task with its exact file scope. Resolve overlapping claims with the actual other session before editing that scope. Use independent Git branches and push commits; Concord carries coordination, not source synchronization. Handoffs must include commit SHA, changed files, executed tests, risks and remaining work, and the recipient's actual acknowledgement. Do not upload production databases, customer materials, credentials or private logs. Delivery capability and immediate wake-up must be established by actual receipts, never inferred from an installed adapter. Existing release authorization and repository checks still apply.

Run `python scripts/dev.py doctor`, inspect current status and fetch before work. Use `python scripts/dev.py start feat/task-name` from a clean checkout; each concurrent task uses its own clone or worktree. Do not alter another task's files or switch its active checkout. Use explicit file staging, meaningful commits, a feature-branch push and a pull request. Read CI results before merging. Never force-push main, rewrite release tags, auto-stash, reset, or overwrite conflicts. An unfinished task must leave its branch, commit, test evidence and remaining work recorded.

Run `python scripts/dev.py setup` once per clone. Hooks are local guardrails; server-side protection and CI are authoritative. Do not bypass them merely to publish faster. See CONTRIBUTING.md and docs/RELEASING.md.

## Product constraints

Stack: Python, PySide6, SQLite and PyInstaller; software version remains 1.0.0. Four project states: 待开发、开发中、待交付、已交付. Urgency, project type and workload eligibility are independent. Never add accounts, cloud data sync, clipboard monitoring, approval workflows or background message reading without a new request.

Intake copies and verifies files; originals and archive basenames remain intact. ZIPs are not extracted or executed. Ambiguous matching needs explicit selection. Immutable delivery events and append-only corrections must be preserved. First valid delivery counts by project and Asia/Shanghai day; re-delivery is history, not another new project. Revoking an effective event returns the project to 待交付; restoring validity alone does not set 已交付. Excluding personal workload is not revoking delivery.

For ordinary document intake, a different revision at the same managed destination keeps the incoming standard filename; rename the previous working copy to a non-overwriting version name and preserve its identity/history. Keep external sources and ZIP/archive names unchanged. Rotation requires identity checks, transaction-bound recovery records, and safe restart recovery; unresolved recovery blocks storage mutations. Never silently suffix the new ordinary document to bypass an unsafe collision.

Keep superseded ordinary documents in a registered same-volume history root outside project/group/archive directories. Current listings and future material delivery exclude that history; preserve existing delivery artifacts and facts. Existing files enter history only through verified rotation or explicit manual selection and confirmation, never filename guessing. History ownership follows the work-file project identity through merge. Protect registered history roots from intake, exports and directory changes; rollback to older code requires a compatible data/material recovery point.

Delivery-table export is read-only and uses one database snapshot. Export one row per canonical project with effective delivery history, including workload-excluded deliveries; do not infer zero from missing archive-count facts. Export never applies project types or changes delivery validity. Recheck current managed-path boundaries before publishing the output file.

Task groups and task items are independent of the original project/batch/directory identity. Numbers and original messages are immutable; new rounds never inherit old completion automatically. Material readiness pins a valid delivery event and its correction version; manual handoff pins the displayed binding and never implies a network send. Group/task totals must not inflate lifetime-first project workload. Archive classification is separately configurable, uses the latest effective delivery per project, and keeps append-only undo-protected audit; unknown facts never mean zero.

The current desktop workflow uses original project states and a project-first workspace. The separate task-group/manual-handoff UI is retired at the user's request; preserve its historical data and backend compatibility without restoring the page. Normal text intake uses receive_text, not receive_task_text. Batch color/code markers are read-only intake associations, can be multiple per canonical project, and must not change project ordering, identity, original batch_id or directories. Whole-delivery export remains available.

Archive classification supports exact and at_least comparisons independently of the ZIP auto-delivery threshold. New classification policies default to exact/16 with automation disabled; migration preserves existing settings with at_least semantics. Export reads the current type and never silently reclassifies. Historical corrections require a current explicit preview and append-only audit. A rollback to older code must pair with a compatible policy/data recovery point, since older code cannot interpret exact comparison.

Workspace cleanup must keep active filters discoverable and clearable, preserve selection/scroll, and expose critical errors and busy cancellation. Low-frequency actions use existing native menus. Ordinary intake success notices are scoped and temporary; warnings, review requests and errors remain persistent. Determine notice severity from structured results, never localized text matching.

Rename/merge needs an explicit current preview and separate confirmation. Preserve original materials and historical delivery identities; do not merge automatically at startup. New default type never backfills old projects. Archive/deletion are reversible project visibility actions, not file deletion.

Use mature Qt controls, native menus and scrolling. Keep QFluentWidgets pinned and preserve selection, scroll and worker shutdown safety. Test Windows through `python -m scripts.run_tests`, not by disabling system-wide crash reporting. Fixtures must be isolated and anonymous. Do not open or modify a person's actual database for tests.

## Change and release discipline

Use apply_patch for source changes. Add and run failing tests, implement the smallest change, then run the affected and full suites. Do not relabel a failed run as passed or silently lower assertions. Record environment skips and warnings. UI changes require actual browser/Qt screenshots, not generated pictures presented as software.

Never commit data, archives, backups, credentials, private screenshots, local absolute paths or installer binaries. Website illustrative demos use fixed examples and must say they do not process real files. Desktop data is not synchronized by Git. Keep download checksums, old release routes, media provenance, site statistics permissions and disabled sponsorship intact.

Keep the checkout lean. Reuse a suitable checkout, dependency environment and one task-local test output directory; do not create a new clone or a full fixture copy for every verification pass. Retain concise results, source identities and checksums separately from rebuildable payloads. Inventory and verify ownership before removing generated artifacts; never treat working materials, archives, recovery points or unfinished journals as disposable caches. Storage changes must avoid generating redundant copies without silently dropping historical material or delivery records.

Release only from reviewed commits on main. Tags and already-published assets are immutable. Installers, source snapshots and website deployments must have explicit source commits and SHA-256 evidence. Test status does not prove a second physical device was tested. Keep local OAuth credentials out of CI and never expose secrets to pull-request code.

<!-- concord:start -->
## Concord — shared work-state for coding agents

<!-- concord:workflow-version=7 -->

This project uses Concord MCP. Keep coordination to the five workflow tools:

- **Before editing**, call `start_work` with the task, your agent kind, and
  expected files or modules. It registers presence, accepts assigned work when
  appropriate, claims the scope, and returns overlap warnings. Concord derives
  your `agent_id` from your session — omit it unless your client told you one.
- Use `inspect_work` to read the workspace, one task, one agent, or one message
  thread. Use `update_work` for durable progress and for live prompts/replies
  to another promptable workspace agent, including while that agent is busy.
- Use `transfer_work` for assignment, acceptance, decline, release,
  reassignment, evidence-bearing handoff offers, and reopening.
- **Before finishing**, call `finish_work` once with the outcome, changed
  files, tests, assumptions, decisions, risks, guardrails, and provenance. It
  records evidence and can mark work review-ready or terminal.
- Before handing work to cloud Devin, use `transfer_work` to hand off or
  release your local claim; cloud Devin cannot access this local Concord state.

Keep each claim small and resolve reported overlaps before editing. Concord
regenerates human-readable review artifacts in `.concord/`.

Enforcement remains client-dependent. `concord doctor` reports setup and
workflow adoption; optional hooks can block exact-file collisions.

Replace <agent-id> with the id the SessionStart hook printed for this session.
A monitor may not inherit the session's environment, so it cannot work the id out itself.

In Grok Build, keep this session reachable while idle by starting one persistent
monitor for `concord inbox watch --agent <agent-id> --provider grok`. Do not start a duplicate when that monitor is active.

In Cursor, start exactly one Cursor background Shell task for `concord inbox watch --agent <agent-id> --provider cursor --once`.
Leave it active when the turn ends. When Cursor resumes you with its completion,
answer the emitted peer message and immediately start a fresh background monitor.

In Gemini CLI, start the exact monitor command supplied by the SessionStart hook
(`concord inbox watch --agent <agent-id> --provider gemini --once`) with `run_shell_command` and `is_background: true`. Project
settings inject background completion into the agent. Answer the peer message,
then immediately start a fresh background monitor.

In Antigravity, start exactly one background command for `concord inbox watch --agent <agent-id> --provider antigravity --once`
and end the turn without waiting on it. Its completion wakes the idle session:
answer the peer message, then immediately start a fresh background monitor.
<!-- concord:end -->
