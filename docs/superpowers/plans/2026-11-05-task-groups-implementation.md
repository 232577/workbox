# WorkBox 任务组、手动交接与分类 Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development task-by-task. This plan is authorized by the user on 2026-11-05; do not request the same classification or handoff decision again.

**Goal:** 将原消息、固定任务编号、本次材料就绪和手动交接连成可核对的主窗口流程，并完成已确认的类型规则、Excel和正式升级。

**Architecture:** 任务组/任务项独立于长期项目与旧物理批次；交付就绪关联明确的不可变交付事件，交接另追加人工动作。旧项目、文件、交付事实、工作量规则保持；类型仅按明确规则窄改并记录可恢复动作。

**Tech Stack:** Python 3.11 / PySide6 6.8.3 / pinned QFluentWidgets / SQLite / openpyxl 3.1.5 / PyInstaller.

**Spec:** 已讨论的2026-11-05消息与任务组规划，用户进一步确认“材料就绪按原规则，发给对方后手动确认已交接”。

## Global Constraints

- 组号G20261105-003、任务号G20261105-003-02，按北京时间发号；不改项目名、文件名、目录名。作废号不回收。
- 同名项目复用身份；本轮任务独立未交接。补充消息显式指定组，重复原文默认定位旧组，明确新一轮才创建新任务实例。
- 材料就绪来自本次明确关联的有效交付；手动交接不可自动从归档推断。多任务共享项目时不自动全完成。
- 交付撤销/恢复动作版本改变后不能悄悄恢复旧任务完成，手工重新关联和确认；历史动作保留。
- 已有项目首次计数、代交资格、撤销交付回待交付规则保持；任务数不混入首次交付项目数。
- 类型规则：最近有效交付单项目归档>=4文件→可运行，0—3→生成材料，未知保持。阈值/类型目标可配置，重复扫描不重写手工类型。
- 只用本地工具；测试全部匿名且自动Qt测试offscreen；不读取系统剪贴板。真实数据只用于授权的只读核对及备份后的分类窄变更。
- 保留既有main/PR/标签和旧资产。正式包不带用户数据；准确main、源码/载荷证明、真实Qt/匿名安装、网站验证、恢复点和真实在线升级分别验证。

## Review Focus

1. 老项目已有交付不能满足新任务；事件绑定保存撤销动作版本，恢复不会自动重新交接。
2. 同文新任务、待核对消息确认、跨日和崩溃重试不能丢组或重复创建实体。
3. 合并项目只归一项目身份，不自动合并任务/交接；误归组保留原号。
4. 自动类型只处理真正新的有效交付；未知、停用目标、受保护项目明确跳过，撤销类型调整不覆盖后续人工修改。
5. 空态、长名称、窄窗、键盘与重复刷新保持选择；导出只读，手动交接不改变计数和文件。

## Task 1 — 任务后端（独立文件所有权）

Create task_groups.py and focused schema/intake/delivery helpers as needed; tests/test_task_groups*.py. Modify desktop_service.py only for property/entrypoints and pending receipt resolution integration.

Interfaces: `service.task_groups = TaskGroups(service)`; `receive(text, *, group_id=None, new_round=False, request_id=None)` returns legacy receipt fields plus group_id/group_code/task_ids; `snapshot(*, query='', open_only=True, received_day=None)` returns groups with tasks/counts; `messages(group_id)`; `delivery_choices(task_ids)`; `bind_delivery(task_ids,event_id,confirmed=False)`; `handoff(task_ids,confirmed=False,reason='')`; `undo_handoff`, `withdraw`, `reopen`, `move_task`, `rename_group`, `set_color`; `history_candidates` and explicit `import_history` (no automatic history completion).

Each task includes id/code/project_id/project_name/material_ready/event_id/archive_path/handed_over/withdrawn/needs_review. Counts include total, ready, handed, pending, withdrawn, needs_review; message review count separate. Snapshot is read-only. Standalone `observe_delivery_event(connection,event_id)` is called by the root integration inside the delivery transaction. It records a unique eligible task binding, or ambiguity candidates; it must not later auto-bind an originally ambiguous event just because another task closed. Event action version and binding identity pin handoff validity.

- [ ] Write/run red tests: numbering/retry/same text/new round/pending receipt/continuation/historical project/no history mutation.
- [ ] Implement the minimal service and append-only relations/actions; create no files beyond original intake behavior.
- [ ] Test material ready, explicit multi-task binding, manual handoff, undo/reopen/withdraw, void/restore, canonical merge, old-data candidate safeguards and cross-day operations.
- [ ] Review and commit with root integration.

## Task 2 — 归档类型后端（独立模块）

Create archive_classification.py and tests/test_archive_classification.py. Root owns database.py hooks. Interfaces: `ArchiveClassification(db).policy()/set_policy(...)`, `.preview()->{token,rows,changed_count,skipped}`, `.apply(token,confirmed=False,enable_future=True)`, `.undo(operation_id,confirmed=False)`; standalone `classify_new_delivery(connection,event_id)` and `record_manual_type_selection(connection,project_ids,before,type_id)`.

New generic installations keep the new rule off until configured; the authorized current-machine rollout explicitly enables the requested rule after backup. Default target suggestions resolve active type names, never hardcode IDs or recreate inactive types. Preview binds latest effective events, current type links, catalog state and audit revision. Apply is one transaction and records old/new IDs. Undo skips later manual intent even if value was selected unchanged. Automatic event keys are idempotent; refresh/restart do not backfill.

- [ ] Red then green on 3/4 threshold, material/combinedZIP counts, repeat/revoke/restore, type inactive/missing, stale preview, no-op/idempotence, undo concurrency and preserved events/files.
- [ ] Supply APIs and hook contract to root; no actual user-data writes.

## Task 3 — 主窗口任务组（UI文件所有权）

Create task_groups_ui.py and tests/test_task_groups_ui.py; modify compact_ui.py only. Add task-groups navigation after existing page indices (preserve0—3), reuse existing file pane/project jump. Expose `task_text_requested(text,group_id,new_round)` signal for root controller to enqueue; all operations use FileJob and preserve shutdown safety. Provide open groups/all/today received, search, stable group color, original messages, explicit continuation/new round, delivery choice, manual handoff/undo/reopen/withdraw/move, and copy unhanded list only on user click. Keep classification UI integration with root-owned separate module via a signal or method, not overlapping file edits.

- [ ] TDD on API calls, no stale selection, empty/error/busy/close, duplicate/new round, source visibility, handoff not automatic.
- [ ] Native-sized layout tests for long names/light-dark/680px and stable expansion/selection/scroll.

## Task 4 — Root integration, exports and classification UI

Modify desktop_controller.py text intake to use task-aware receive while preserving existing payloads; notification navigates exact group/task. In database._append_delivery_event call classification and task observer only when their schema exists, within the original transaction. In set_project_types record manual intent in the same transaction. Add inline archive classification pane with policy/preview/apply/undo and safe defaults. Extend Excel to task group and task detail sheets while keeping unique-project sheets and read-only semantics. Update tests and docs; no blanket weakening of existing assertions.

- [ ] Resolve cross-module API drift and test real receive→archive→ready→manual handoff→export→void/restore/rework flows.
- [ ] Run affected and complete isolated regression, repo guard and website tests; native source Qt exercise and independent code review.

## Task 5 — Release and authorized current-machine rollout

- [ ] Update PR39 scope around final implementation; six CI checks and exact head review, normal merge only.
- [ ] Fresh locked build from reviewed main; source archive/frozen module/payload proof, clean installer and native same-EXE intake/group/handoff/export tests.
- [ ] New immutable tag/assets, website stable catalog PR and deployment, anonymous complete/Range downloads; retain every prior asset.
- [ ] Current machine recovery preflight/normal exit/verified snapshot; actual installed-client online update and restart, payload/version/data/history verification.
- [ ] Read-only classification preview on preserved live data; apply only authorized known rows and enable future rule with audit; verify exact permitted type differences and no other business/file/history changes.
- [ ] Update Issue7 with precise commit/build/site/install/verification and limitations. Do not label prior-source or source-only evidence as frozen acceptance.
