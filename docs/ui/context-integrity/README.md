# 文件读取状态与交付上下文：匿名原生验证

2026-11-09，Windows Qt 图形后端，两个匿名项目和四个模拟文本文件，独立临时数据库。以下图片来自当前运行窗口；`evidence.json` 记录 8 个相关源码模块与 8 张图像的 SHA-256，拍摄前后源码一致。

| 场景 | 浅色 | 深色 |
| --- | --- | --- |
| 刷新失败，保留上次结果 | [实图](light-files-last-read.png) | [实图](dark-files-last-read.png) |
| 读取异常，总数未知 | [实图](light-files-unknown.png) | [实图](dark-files-unknown.png) |
| 部分位置不可读 | [实图](light-files-partial.png) | [实图](dark-files-partial.png) |
| 窄窗交付明细的所属项目 | [实图](light-delivery-project.png) | [实图](dark-delivery-project.png) |

文件页为 1200×780，交付页为 660×480，100% 缩放。刷新失败通过实际后台读取任务注入匿名异常，其余不完整结果使用受控数据；未打开个人数据库。独立审查核对图像、源码摘要及提示可读性。Qt 事件回归另验证长名换行、宽窗恢复和旧上下文清理；截图不代替交互验收，也不表示已发布安装版或另一台设备验收。
