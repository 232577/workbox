# Batch Delivery Progress Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent read model and UI tasks; retain an independent integration review.

**Goal:** 在原项目列表中直接提示同批项目已交付和剩余数量，合并已进入main的PR #49成果，测试后更新正式安装。

**Architecture:** 复用既有收件批次只读索引和canonical项目身份，在同一数据库快照计算每批唯一项目的当前状态。列表标记、批次筛选和选中批次摘要共用这份统计；不增加持久状态、表或任务页面。

**Tech Stack:** Python、SQLite、PySide6、已锁定QFluentWidgets及原生控件。

**Spec:** 本文下述范围与口径，以及DESIGN.md和docs/PROJECT-BATCH-MARKERS.md。

## 范围与口径

- 以最新main为基线；PR #49已合并为b4ded64fbe0066c9d8e29d75462cca61c4da5c88，不重复合并或修改其任务分支。
- 保留原四状态、项目排序、一项目一行和同批颜色编号；不恢复任务组页面，不弹出打断工作的提醒。
- 当前状态为“已交付”的项目算已交付，其他三个状态算未交付。与个人工作量资格、首次交付日期无关。
- 按canonical项目身份在每批去重。同项目可以属于多批，各批按各自完整成员计算；项目合并后不重复计数。
- 已删除项目不再占当前批次待办；归档仅改变可见性，仍属批次并参与统计。恢复删除后重新计入。
- 数量在状态、搜索、类型或归档可见性筛选之前计算，不得把“只显示待交付”误算成整批总量。异常来源保留原警告，不猜关联。
- 批次小标签显示原编号及“余N”；为零时显示“已齐”。完整提示/无障碍说明显示“已交付X/Y，剩余Z”。选中批次时在左栏显示相同摘要；所有批次下不增加大块说明。
- 更新项目状态、收件自动交付、撤销误记、合并或删除后，正常刷新同批全部可见行的数量。只读显示不改文件、交付历史、类型或工作量。
- 本机更新前使用既有备份机制，保留所有现有数据和配置。公开交接仅包含提交、公开资产及匿名验证信息。

## Task 1: 批次快照统计

**Files:** project_batches.py、desktop_service.py（仅project_workbench_snapshot）、tests/test_batch_delivery_progress.py。

**Interface:** 每个batch marker和batch_options项新增整数total_count、delivered_count、remaining_count。原key/code/label/color/source身份不变。

- [ ] 用匿名夹具先验证部分交付、全交、未交、代交不计、跨状态筛选、共享项目多批、canonical合并、删除/恢复、归档和来源异常。
- [ ] 在已存在读取事务中一次性聚合；查询数量不能随项目数增长。不写库、不修改传入对象或旧数据。
- [ ] 运行新增测试及原批次/合并/交付相关回归。

## Task 2: 原界面中的紧凑提示

**Files:** compact_ui.py、fluent_views.py、tests/test_batch_progress_ui.py、DESIGN.md、docs/PROJECT-BATCH-MARKERS.md。

- [ ] 沿PR #49的清单工具布局添加选中批次摘要；批次行标记保持完整编号并显示余量。
- [ ] 旧/缺少统计字段的匿名行保持原标签，未知值不得显示0。计数变化纳入刷新签名，保留当前选择、滚动、分栏、排序和有效筛选。
- [ ] 验证状态改变和撤销后的同步刷新，660px窄窗及浅深色，长编号、多批标记、键盘与无障碍说明。
- [ ] 更新现有设计说明，不增加新配色/字体系统。

## Task 3: 合并验收与正式升级

- [ ] 独立评审本轮改动及PR #49更新交接风险，修复后运行完整本机回归与网站检查。
- [ ] 新任务PR检查通过后合入准确main；若main前进，显式合并并复测，不覆盖另一端提交。
- [ ] 从冻结main构建新标签的干净安装包，完成真实匿名Qt与安装验收，保留失败记录。
- [ ] 核对旧版公开资产后发布新包，同步官网稳定清单，验证下载字节、旧链接和权限边界。
- [ ] 备份原正式目录和数据，通过已安装程序真实在线升级，核对版本、文件、数据库/配置、交付历史与窗口结果。
- [ ] 写回#7准确公开交接；全部私有证据留在本机。
