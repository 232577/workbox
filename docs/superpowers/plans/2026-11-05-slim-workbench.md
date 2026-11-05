# Workbench Simplification Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans; each task includes regression, implementation and review.

**Goal:** 让常用收件、交接与导出操作更直接，把维护功能集中到高级设置。

**Architecture:** 继续使用 CompactPanel、TaskGroupsPage 和现有 Qt/Fluent 控件。只改导航与动作组织，所有菜单调用原服务/页面，数据库和文件语义不变。

**Tech Stack:** Python3.11、PySide6、锁定 QFluentWidgets、SQLite。

**Spec:** `docs/WORKBENCH-SIMPLIFICATION.md`

## Global Constraints

- 不删除业务功能、数据或配置，不改变交付、交接、分类和导出范围。
- 不新增布局模式开关；保留原窗口尺寸、主题、原生滚动和上下文选择。
- Qt回归使用offscreen/pyside6和各自独立临时目录，真实UI使用匿名夹具。

## Review Focus

- 有待核对项时不因入口收起漏掉提醒；准确反馈导航仍可达。
- 全局设置动作与所选任务操作分开，切页后不得作用到错误项目。
- 处理中旧按钮禁用必须同步到新菜单，关闭仍等待原工作线程。
- 隐藏的原控件不能在窗口缩放/刷新后再次冒出。
- 浅深色、660px窗口与键盘菜单都保留清晰名称和可见焦点。

### Task1: CompactPanel高级设置与日常导航

**Files:** `compact_ui.py`; new `tests/test_slim_workbench.py`; affected layout/navigation/settings tests.

**Interface:** `CompactPanel.advanced_menu`提供原回调动作；`TaskGroupsPage.import_history()`供全局入口调用。`settings_button`稳定无障碍名称为“高级设置”。原ZIP/待核对/交付页索引保持。

- [ ] 写失败测试：主界面只常驻三页与高级设置、隐藏旧工具按钮；高级菜单可达原工作设置/ZIP/待核对/日志/监视/分类/历史/帮助/更新/外观。
- [ ] 覆盖待核对0→1→0、正在核对时保留入口、准确receipt定位、忙碌禁用与缩放后隐藏状态。
- [ ] 复用已有控件及回调实现，不修改后端或增删业务表。
- [ ] 运行新回归和受影响导航、设置、交付、生命周期回归，记录结果。

### Task2: 任务组主要动作与上下文维护菜单

**Files:** `task_groups_ui.py`; `tests/test_task_groups_ui.py`, `tests/test_task_group_usability.py` and focused new menu tests.

**Interface:** 新增可见 `export_button`调用已有`export_group()`；`import_history()`封装原`_history()`，仍沿用忙碌/退出保护；全局分类信号兼容保留但不占任务菜单。

- [ ] 写失败测试：四个主要操作加更多、复制移菜单、全局功能不重复出现；高级任务子菜单仍绑定当前任务。
- [ ] 验证实际导出组ID、未选提示、忙碌状态、取消、窗口重排及长名/选择/滚动保持。
- [ ] 使用原生QMenu分层并复用原回调，不改变操作确认或后台服务。
- [ ] 运行任务组、工作量、导出相关回归。

### Task3: 集成、验收与既有发布流程

**Files:** `DESIGN.md`, `docs/UI-DESIGN-SYSTEM.md`, `docs/TASK-GROUPS.md`, `update_client.py`; private release/native scripts.

- [ ] 文档与最终入口一致；独立复核Task1/2，先修问题再冻结。
- [ ] 本轮完整回归、真实Qt主/高级入口、待核对和上下文操作、浅深窄窗验证。
- [ ] 新日期构建标识核对无碰撞；PR/六项CI通过后只从准确main构建、匿名安装及同EXE验收。
- [ ] 公开新包、官网稳定清单和匿名下载核对，保留旧版；既有恢复点机制与原客户端实际在线升级，核对所有原数据配置及交付历史。
