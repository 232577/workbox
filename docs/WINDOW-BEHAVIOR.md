# 悬浮入口与窗口恢复

本页描述源码开发版，正式安装包以发布记录为准。

## 日常操作

- 单击悬浮球、点击托盘或再次启动，同一数据目录复用已有工作台。首次打开靠近悬浮球，之后保留用户位置和尺寸；移动球不搬动工作台。
- 普通、最小化、最大化、关闭后重新打开都保留原有窗口状态。关闭和后台运行只隐藏工作台，当前搜索、筛选、选中项目、滚动和页面保持；退出后再次启动恢复窗口位置、尺寸和最大化状态。
- 有模态对话框时优先唤回该框，不切换底层页面或隐藏未保存输入。退出仍使用原有安全停机流程。
- 显示器、可用区域或缩放变化后，越界入口和普通窗口回到可操作区域；合法跨屏布局、负坐标屏幕和贴边位置保持。最大化区域由 Qt 和系统管理。
- 进入后台后，仅每两秒检查托盘可用性；失效时恢复不抢焦点的悬浮入口，主窗口保持隐藏，文件任务和归档监测继续。停止后台或退出后取消检查。

## 实现与兼容

窗口复用现有 `CompactPanel`。Qt `saveGeometry/restoreGeometry` 的状态以有长度限制的十六进制字符串保存在既有设置表 `manager_window_state`；`manager_geometry` 继续记录普通窗口几何，兼容旧版。损坏或未来版本的 Qt 状态回退到旧几何。无需数据库结构迁移，不改变项目或材料数据。

显示变化合并后处理，不增加空闲数据库扫描或动画。后台反馈不激活工作台；用户主动唤回才请求系统激活。Windows 最终控制跨应用焦点，不使用强制置顶循环绕过系统限制。

## 验证与边界

专用回归为 `tests/test_window_lifecycle.py`、`tests/test_window_environment.py` 和 `tests/test_window_recovery.py`，覆盖窗口恢复、模态优先、上下文保留、旧设置兼容、小屏/负坐标/跨屏边界、托盘销毁、监测及安全停机。

使用 `python -m scripts.run_tests` 执行。Windows 原生测试设置 `QT_QPA_PLATFORM=windows`，常规 CI 使用 `offscreen`。显示器事件和部分几何由测试模拟，不能代替物理混合 DPI 双屏拔插、Explorer 重启或跨应用焦点验收；也不代表已重新打包安装。

本次不加入全局快捷键、吸边自动隐藏或新的中间面板，避免改变现有单击打开路径。

交互依据：[Everything 已有窗口唤起](https://www.voidtools.com/support/everything/keyboard_shortcuts/)、[PowerToys 显示位置与激活设置](https://learn.microsoft.com/en-us/windows/powertoys/command-palette/settings)、[Qt 6.8 窗口状态恢复](https://doc.qt.io/qt-6.8/restoring-geometry.html)。
