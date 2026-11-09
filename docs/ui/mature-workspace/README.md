# 项目工作台与控件：匿名原生界面验证

2026-11-09，在 Windows 的实际 Qt 图形后端运行当前源码，使用临时隔离数据库、110 个匿名项目和模拟文件。以下图片直接来自运行中的窗口，不是设计稿；`evidence.json` 保存七个界面模块及保留图片的 SHA-256。

| 场景 | 实图 |
| --- | --- |
| 宽窗项目与文件 | [浅色](light-1200-projects.png)、[深色](dark-1200-projects.png) |
| 窄窗清单与长名称详情 | [清单](light-660-queue.png)、[详情](dark-660-long-name.png) |
| 筛选、空态与读取失败 | [筛选](light-1200-filters.png)、[空态](light-1200-empty.png)、[失败](light-1200-file-error.png) |
| 交付主表与明细 | [宽窗](light-1200-delivery.png)、[窄窗主表](dark-660-delivery.png)、[窄窗明细](dark-660-delivery-details.png) |
| 日历 | [浅色](light-calendar.png)、[深色](dark-calendar.png) |
| 设置与数字步进 | [浅色](light-settings-lower-controls.png)、[深色](dark-settings-lower-controls.png) |

通过真实控件回归检查分栏拖动下限、Tab 连续导航、窄窗返回的选择和阅读位置、日期弹出点击区、明细收起和比例恢复。独立评审发现并修复按钮焦点态图文重叠、项目栏过窄裁切、隐藏页签焦点阻塞和设置说明裁切。

图片为 100% 缩放；125%/150% 的控件几何回归不等同于第二台物理设备的实拍。系统界面自动化仅成功验证匿名项目搜索和清除，坐标点击与系统截图接口不可用；其余交互通过 Qt 事件回归及原生窗口图像验证。本轮没有使用真实业务库、发布安装包或升级用户安装。测试结果与历史失败边界见 [VERIFICATION.md](../../../VERIFICATION.md)。
