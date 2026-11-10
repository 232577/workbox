# 悬浮入口与窗口恢复

本页描述源码开发版，正式安装包以发布记录为准。

## 日常操作

- 单击悬浮球、点击托盘或再次启动，同一数据目录复用已有工作台。首次打开靠近悬浮球，之后保留用户位置和尺寸；移动球不搬动工作台。
- 普通、最小化、最大化、关闭后重新打开都保留原有窗口状态。关闭和后台运行只隐藏工作台，当前搜索、筛选、选中项目、滚动和页面保持；退出后再次启动恢复窗口位置、尺寸和最大化状态。
- 有模态对话框时优先唤回该框，不切换底层页面或隐藏未保存输入。退出仍使用原有安全停机流程。
- 显示器、可用区域或缩放变化后，越界入口和普通窗口回到可操作区域；合法跨屏布局、负坐标屏幕和贴边位置保持。最大化区域由 Qt 和系统管理。
- 进入后台后，仅每两秒检查托盘可用性；失效时恢复不抢焦点的悬浮入口，主窗口保持隐藏，文件任务和归档监测继续。停止后台或退出后取消检查。

## 可选快捷键

在“高级设置 → 工作设置”的“窗口与快捷键”分区启用，也可搜索“快捷键”。初始关闭；输入框中的 `Ctrl+Alt+W` 只是建议组合，点击“应用快捷键”后才注册并保存。至少选择 Ctrl、Alt、Shift 中两个，再加字母、数字或 F1–F11；不使用 Windows 保留键。

从其他软件按组合键会唤回工作台，工作台已在前台时再次按下则收起并保留悬浮球。最小化和最大化沿用原恢复状态；有未完成的模态框时优先唤回该框，不隐藏输入。菜单打开期间不执行切换。

快捷键独立保存，工作设置底部的“取消”不撤销已应用的快捷键；取消勾选后也需点击“应用快捷键”。组合被占用时不抢占其他程序，也不替换当前生效组合。设置页分别反映实际生效和已保存的组合；保存或恢复失败会提示差异，并允许重试。

退出正常释放注册。释放失败时保留工作台和数据会话，恢复入口与监视并说明失败，允许再次退出；安装更新的停机失败仍走原有恢复流程。第二实例只唤回已有窗口，不注册另一组快捷键。

## 实现与兼容

窗口复用现有 `CompactPanel`。Qt `saveGeometry/restoreGeometry` 的状态以有长度限制的十六进制字符串保存在既有设置表 `manager_window_state`；`manager_geometry` 继续记录普通窗口几何，兼容旧版。损坏或未来版本的 Qt 状态回退到旧几何。无需数据库结构迁移，不改变项目或材料数据。

显示变化合并后处理，不增加空闲数据库扫描或动画。后台反馈不激活工作台；用户主动唤回才请求系统激活。Windows 最终控制跨应用焦点，不使用强制置顶循环绕过系统限制。

快捷键使用 Windows `RegisterHotKey` 和 `WM_HOTKEY`，没有键盘钩子、按键轮询或键入内容记录。启用 `MOD_NOREPEAT` 防止长按连发，排队事件在改键、停用及退出后失效；Qt 对象销毁也释放注册。配置保存在既有设置表的 `window_shortcut`，空字符串代表关闭。

## 验证与边界

专用回归为 `tests/test_window_lifecycle.py`、`tests/test_window_environment.py` 和 `tests/test_window_recovery.py`，覆盖窗口恢复、模态优先、上下文保留、旧设置兼容、小屏/负坐标/跨屏边界、托盘销毁、监测及安全停机。

快捷键回归位于 `tests/test_global_shortcut.py`、`tests/test_shortcut_settings.py` 和 `tests/test_window_shortcut.py`。覆盖默认关闭、冲突、保存/释放失败、回滚、对象销毁、消息隔离、单实例、正常/延迟退出、设置搜索及独立保存。真实 Windows 子进程核验系统注册、冲突、释放和 Qt 消息派发；普通与 200% 缩放核验浅深主题控件。

使用 `python -m scripts.run_tests` 执行。Windows 原生测试设置 `QT_QPA_PLATFORM=windows`，常规 CI 使用 `offscreen`。显示器事件和部分几何由测试模拟，不能代替物理混合 DPI 双屏拔插、Explorer 重启或跨应用焦点验收；也不代表已重新打包安装。

物理键盘长按和跨应用焦点仍需实机验收。尚不加入吸边自动隐藏或新的中间面板，保留现有单击打开路径。

交互依据：[Everything 已有窗口唤起](https://www.voidtools.com/support/everything/keyboard_shortcuts/)、[PowerToys 显示位置与激活设置](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/settings)、[Qt 6.8 窗口状态恢复](https://doc.qt.io/qt-6.8/restoring-geometry.html)。

快捷键依据：[Microsoft RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)、[WM_HOTKEY](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-hotkey)、[Qt 原生事件过滤](https://doc.qt.io/qtforpython-6.8/PySide6/QtCore/QAbstractNativeEventFilter.html)。
