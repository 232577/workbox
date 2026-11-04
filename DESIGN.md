# WorkBox 界面规范索引

桌面设计的主规范是 [桌面设计系统](docs/UI-DESIGN-SYSTEM.md)，网站另使用 [网站设计记录](website/DESIGN.md)。本文件记录本轮新增交互的归属，不另建配色或字体系统。

- 桌面使用 PySide6、锁定的 QFluentWidgets 与原生窗口；浅深色、字号和控件外观由 `theme.py`、`control_styles.py`、`ui_components.py` 负责。
- 交付页保持在主窗口，表格和选择由 `fluent_views.py`、`delivery_ui.py` 负责；继续使用原生像素滚动、手动列宽和已有窄窗切页。
- 新增“导出表格”复用 `compact_ui.button`，无障碍名称为“导出全部有效交付项目”。保存位置使用 Qt 原生文件对话框；只在最终路径存在文件时确认替换。
- 后台任务沿用 `ui.FileJob`。导出期间禁用重复操作，完成后留在原页面并显示项目数和位置；失败与取消不显示成功，也不发出统计变更信号。
- 只读报表归属 `delivery_export.py`；归档数量事实归属 `archive_facts.py`，交付有效性与合并关系归属 `Database.delivery_history`。报表和数量使用同一读取事务。
- 核对保存取消、覆盖确认、成功、失败、忙碌关闭、无结果仍导出全部、窄窗口、长名称及真实保存对话框。测试使用匿名数据，Qt自动回归使用offscreen，不操作系统剪贴板。
