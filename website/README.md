# 工作箱下载站

静态页面位于 `public/`。源码与版本安装包保存在 GitHub 仓库 `232577/workbox`。
Cloudflare Worker 使用固定 GitHub Release 地址流式提供同域名下载，并支持单段断点续传。
下载不需要登录，不传递访问者 Cookie 或 Authorization；网站不接收项目文件。

侧栏图标对齐版：独立标签 `v1.0.0-enhanced-sidebar.20261029`，安装包名
`WorkBox-1.0.0-Enhanced-Sidebar-20261029-Setup-x64.exe`；目录保留七个历史版本。
安装包大小为39,685,611字节（约39.7MB），SHA-256为 `7f04ad22dbb30dc61eaa1f2149ad6c65dcca7044e18b9b4fbb3ed21d11d01a5a`。
网站部署后仍需核对完整下载、媒体来源、播放与拖动进度，不以本地检查代替线上验收。
公开说明见 `../publishing/sidebar-20261029/`；旧 `publishing/` 根文件保留历史内容。

`/stats/` 提供仅所有者可看的浏览量、下载开始次数和每日趋势。
统计口径、密钥设置和验收方法见 [ANALYTICS.md](ANALYTICS.md)。
站内下载量不包含 GitHub 直链下载；备用链接点击单独列出。

赞助模块由 `public/support.json` 控制，默认关闭。只有所有者提供并核验本人收款码后才启用；
微信/支付宝图片放在 `public/support/`，图片未配置、加载失败或配置不合法时入口隐藏。
赞助完全自愿，不影响免费下载使用；手机号不能替代已经确认的收款二维码。

## 本地检查

```powershell
node --test website/*.test.mjs
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

首页由 `public/index.html`、`homepage.css`、`homepage.js` 组成，继承公共基础样式，
但不改变统计页布局。四步流程支持键盘切换，无 JavaScript 时仍显示全部内容。
官网只说明桌面功能，不接收项目文件，也不能在网页上修改桌面工作规则。

首次引导步骤视频来自已验证冻结程序的隔离项目截图，标注“截图步骤演示”，含WebVTT字幕，
无自动播放。`MEDIA.json`记录已审核媒体的构建来源、大小与SHA256。
MP4请求先经过Worker，声明字节范围并正确返回206/416，以支持浏览器拖动进度；
教程读取不进入安装包下载统计。媒体大小限制为25 MiB，其他静态资源仍由Assets处理。
