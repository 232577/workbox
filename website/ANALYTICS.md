# 站点统计

用户选择仅所有者可见。`/stats/` 是登录和查看入口；数据接口始终检查服务端会话。
密钥只放在 Cloudflare Worker Secret `STATS_ADMIN_KEY`，不放源码、静态文件或网址。
登录使用 HTTPS，签名会话 Cookie 有效 8 小时，设置 Secure、HttpOnly、SameSite=Strict。

## 统计口径

- 页面浏览：下载页可见时上报一次匿名事件；刷新会形成新的浏览，不是独立访客人数。
- 站内下载：固定安装包上游返回有效的 200 或从零开始的 206 后记录开始，不代表下载完成或安装。
- 每次下载使用随机标识，同一页面会话在 30 分钟内沿用；HEAD、失败响应和非零续传片段不增加下载量。
- GitHub 备用下载只单列链接点击，直接访问 GitHub 的下载不计入站内下载量。
- 北京时间按日汇总；随机事件标识去重保留 30 个日历日，汇总长期保留；不存访客 IP、身份、设备或项目数据。
- 从启用时开始记录，不推算、补造此前浏览。脚本屏蔽、网络故障和自动访问可能影响计数。

SQLite Durable Object `SiteMetrics` 通过内部绑定写入，去重和计数共用事务。
下载统计放入 `waitUntil`，统计服务故障不阻断文件响应。
管理员验收使用带有效登录和 `X-WorkBox-Validation` 随机 UUID 的独立对象，不计入正式流量。
未授权验收标识不会回退到正式计数。

## 验证和发布

运行 `node --test website/worker.test.mjs website/tracking.test.mjs website/analytics.test.mjs`。
预览使用隔离数据库和开发密钥；生产密钥用 `wrangler secret put STATS_ADMIN_KEY` 输入。
部署前保留旧 Worker 版本号；迁移新增 SQLite 对象，不删除现有数据或更换统计对象名。
上线后核对匿名统计接口拒绝访问、登录/退出、重复上报、HEAD/续传、完整下载摘要与后台每日汇总。
公开源码仅同步网站白名单，不携带本机密钥、运行日志、数据库或截图。
