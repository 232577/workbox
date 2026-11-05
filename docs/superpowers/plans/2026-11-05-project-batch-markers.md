# Project Batch Markers Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans, with independent review and current validation.

**Goal:** 在原项目列表上显示同批颜色编号，移除独立任务组工作流。

**Architecture:** 保留现有ProjectList、Fluent委托、项目身份及排序。增加只读批次索引和同事务列表快照；UI仅绘制标记并沿原项目状态操作。旧任务数据及历史导出继续可读。

**Tech Stack:** Python3.11、PySide6、锁定QFluentWidgets、SQLite。

**Spec:** `docs/PROJECT-BATCH-MARKERS.md`

## Global Constraints

- 保持原排序，一项目一行，无组标题/重复项目/新页面，无文件目录与状态迁移。
- 不重分类、不重放合并、不删除任务/交付/配置，真实数据不作为测试夹具。
- 只读索引为批量查询，拒绝界面逐项目/逐任务N+1连接；旧数据缺项明确处理。
- 所有自动Qt回归offscreen/pyside6、独立临时目录；真实UI匿名数据且实际选中验证。

## Review Focus

- 文件先建项再整批粘贴，复用项目与新项目必须共享该次收件标记。
- 旧任务移组/合并不能被旧receipt反向恢复，也不能重复项目行或工作量。
- 去掉任务页面后不留下自动首屏、反馈跳转、维护入口及隐藏页面worker。
- 深浅/窄窗中选择与批次颜色可区分，长名、滚动与全选不受新增标记影响。
- 大历史数据与坏来源不阻塞或伪造归属，旧任务编号不误建项目。

### Task1: 只读批次索引和项目快照

**Files:** new `project_batches.py`; `database.py`, `desktop_service.py`; new focused backend tests.

**Interfaces:**
- `read_project_batch_index(connection)` -> `{by_project: {int: list[marker]}, options: list[marker], warnings: list[str]}`；要求已开启读取事务，不写表、不读文件。
- marker必含`key,code,label,color,source_kind,source_id,received_at`，key为带来源类型的稳定字符串，label紧凑、code完整。
- `DesktopService.project_workbench_snapshot(query='',status=None,batch_key=None,urgent_only=False,*,archived=False,due_filter=None,today=None,deleted=False,type_id=None)` -> `{projects: rows, batch_options: markers, warnings: strings}`。rows原字段+`batch_markers`，原SQL排序保持。筛选/编号搜索与标记一致。
- `Database.list_projects(..., connection=None)` 可选调用方事务，沿用delivery_history的同库/事务校验；默认调用方式兼容。

- [ ] 先写失败测试覆盖规范的归属、排序、只读/事务、合并移组及千级批量查询。
- [ ] 实现索引和快照，不调用TaskGroups.import_history或完整task state计算。
- [ ] 旧任务编号文字安全进入核对，不能误建含编号的项目；普通新收件不新增任务记录。
- [ ] 定向回归与独立审查，记录真实结果。

### Task2: 项目色号渲染及移除任务页面

**Files:** `fluent_views.py`, `compact_ui.py`; focused marker/navigation/layout tests.

**Interfaces:** `PROJECT_BATCH_MARKERS_ROLE`承载marker列表。populate保持UserRole为project id，增量更新标记；项目快照一次提供列表和批次选项。TaskGroupsPage不实例化、不作为首屏或高级弹窗。保留全部交付导出、目录/文件/类型操作与异常提醒。

- [ ] RED：原序不变、同批跨状态同色同号、单行多批、窄窗/两主题、选择和滚动保持。
- [ ] 用既有成熟列表/委托添加小型色标和编号，最多两个可见标记及+N，完整tooltip/a11y信息；不重写滚动或选择。
- [ ] 删除任务页及其导航/维护入口；项目默认首屏，分类返回项目，原文定位和批次筛选对应新索引。
- [ ] 验证右键、双击、全选、准确项目定位，不把标记当可操作项目。

### Task3: 收件反馈、导出、文档与发布

**Files:** `desktop_controller.py`, `delivery_export.py`, docs/DESIGN, version; private release/native helpers.

- [ ] 文字收件回到receive_text；移除task_text_requested链路及task-page worker管理。准确反馈定位全部本次项目（含复用），不跳任务页。
- [ ] 全部交付项目表补收件批次编号，保持单事务、只读、历史附表及文件保护。
- [ ] 更新设计/使用说明与实际入口；独立复核后完整回归及实际Qt匿名验收。
- [ ] 准确main构建新包；退休task_groups_ui不再作为必需运行模块，证明覆盖实际全部运行模块并明确退休模块不入运行路径。
- [ ] 沿既有恢复/安装/发布流程完成同包验收、官网/GitHub与当前客户端更新，原数据全量核对并写回#7。
