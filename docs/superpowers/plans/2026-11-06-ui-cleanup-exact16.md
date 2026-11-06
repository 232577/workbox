# UI Cleanup and Exact-16 Classification Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development with scoped file ownership and independent review.

**Goal:** 减少常驻冗余元素，并支持单项目归档恰好16件的可运行分类。

**Architecture:** 保留原Qt控件和项目工作流，按选择/状态显示操作；分类服务增加兼容的比较方式，导出保持只读。正式历史纠正复用现有预览、确认、审计和撤销。

**Tech Stack:** Python3.11、锁定PySide6/QFluentWidgets、SQLite。

**Spec:** docs/UI-CLEANUP-EXACT16.md

## Global Constraints

- 不改原排序、四项目状态、批次归属、文件路径/名称或交付计数；任务组不恢复。
- 自动交付门槛不等于归档类型规则，必须分别验证。
- 已有类型和用户配置不在启动/迁移/导出时静默回填；当前设备纠正独立执行且保护手工选择。
- Qt自动测试offscreen/pyside6，独立临时目录；真实界面用匿名实例，私有日志不公开。

## Review Focus

- 隐藏的活动筛选让用户误以为资料丢失；必须提示并可清除。
- 菜单复用时对象ID或已填原因丢失，导致撤销和代交混淆。
- 16件合包总数不能代替单项目数量，未知不能归成生成材料。
- 旧比较方式/自定义阈值迁移变化，或新UI遗漏比较方式而回退旧算法。
- 空态精简不能隐藏读取错误、待核对与后台工作中取消/安全退出。

### Task 1: 分类比较方式

**Files:** archive_classification.py; tests/test_archive_classification_exact.py; 相关已有分类/策略测试。

- [ ] 先写exact16、旧库旧API兼容、未知/合包/补交/预览过期/撤销与手工保护失败测试。
- [ ] policy comparison取exact/at_least；旧行默认at_least，新行显式exact/16；set_policy仅关键字comparison，旧显式阈值调用保留at_least。
- [ ] 公用规则进入预览与新增交付，审计记录本次规则；导出不推断类型或写库。
- [ ] 定向回归与独立审查。

### Task 2: 项目/文档/ZIP/核对页面精简

**Files:** compact_ui.py, project_documents_ui.py; 新聚焦UI测试和受影响现有断言。

- [ ] RED覆盖活动筛选提示/展开/清除、批次直接可达、原选中滚动与空态/错误动作。
- [ ] 复用现有控件和回调，去重复计数/ZIP0/常驻说明，按选择显示动作。
- [ ] ZIP普通/合包分别验证真实操作，隐藏无效待核对搜索但保留项目查询。
- [ ] 窄窗浅深匿名真实窗口检查。

### Task 3: 交付页面精简

**Files:** delivery_ui.py; tests/test_delivery_cleanup_ui.py和受影响交付测试。

- [ ] RED覆盖筛选菜单、全量导出、类型统计、原因草稿与两种纠正语义。
- [ ] 宽窄复用已有筛选/操作菜单，记录优先、低频操作收起；错误直接可重试。
- [ ] 保留日期编辑、统计、不计工作量、撤销与恢复、线程退出契约并回归。

### Task 4: 集成及交付

**Files:** archive_classification_ui.py, DESIGN.md/设计说明/规则说明/版本；私有发布工具。

- [ ] 分类UI增加比较方式和准确说明，修正旧“返回任务组”残留；验证保存/取消/加载与预览。
- [ ] 源码审查、完整回归与新包真实安装/窗口验收；准确main构建，不覆盖旧标签资产。
- [ ] 既有恢复机制保全；本机显式策略设置及私有盘点候选重核，按即时令牌执行并验证全部受保护事实。
- [ ] 官网/GitHub同步、匿名下载及当前已装程序升级，实际版本与文件/数据库/交付历史核对，回写#7。
