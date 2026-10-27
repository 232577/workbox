# 工作箱下载站

静态页面位于 `public/`。源码与版本安装包保存在 GitHub 仓库 `232577/workbox`。
Cloudflare Worker 使用固定 GitHub Release 地址流式提供同域名下载，并支持单段断点续传。
下载不需要登录，不传递访问者 Cookie 或 Authorization；网站不接收项目文件。

`/stats/` 提供仅所有者可看的浏览量、下载开始次数和每日趋势。
统计口径、密钥设置和验收方法见 [ANALYTICS.md](ANALYTICS.md)。
站内下载量不包含 GitHub 直链下载；备用链接点击单独列出。

## 本地检查

```powershell
node --test website/worker.test.mjs website/tracking.test.mjs website/analytics.test.mjs
npx wrangler@4.127.1 dev --config website/wrangler.jsonc
```

## 发布

先核对 `wrangler whoami` 的账户与目标域名，在 GitHub 发布对应版本安装包，
再对 `website/wrangler.jsonc` 执行 `wrangler deploy`。配置不包含账号 ID、令牌或其他凭据。
发布后以未登录请求完整下载安装包，并与 `release.sha256` 校验；不能只以首页可访问判定下载成功。

版本升级时同步修改页面版本信息、`worker.mjs` 中的固定版本、文件大小与 SHA256。
已发布的版本文件不替换；为后续安装包使用新的版本标签和文件名。

Cloudflare 静态资源单文件上限低于本安装包大小，所以安装包放在 GitHub Releases，
不混入静态网站目录。页面始终提供 GitHub 备用下载链接。
