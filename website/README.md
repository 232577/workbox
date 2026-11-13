# WorkBox 官网

官网介绍桌面工具并提供下载，不接收项目文件。

- 首页 `/`：产品用途、实际界面和下载。
- 指南 `/guide.html`：安装、日常使用、恢复与媒体来源。
- 更新记录 `/releases.html`：当前发布和可展开的历史安装包。
- 所有者统计 `/stats/`：独立登录；口径与权限见 [ANALYTICS.md](ANALYTICS.md)。

产品说明见[仓库首页](../README.md)，逐版变更见 [RELEASE_NOTES.md](../RELEASE_NOTES.md)。整理前的完整说明保存在[历史公开说明存档](../docs/releases/RELEASE-NOTES-ARCHIVE.md)。

当前包身份以[发布目录](worker.mjs)和[公开下载与校验信息](https://workbox.ltxiangzi.dpdns.org/releases.html)为准，维护说明不另存一套易过期的版本数据。

## 文件职责

| 文件 | 用途 |
| --- | --- |
| `public/index.html`、`guide.html`、`releases.html` | 三个公开页面，静态内容在无脚本时仍可读。 |
| `public/release-page.css` | 产品与阅读页的最终视觉；不接管统计页。 |
| `public/homepage.js` | 旧章节链接兼容、折叠内容定位等渐进增强。 |
| `worker.mjs` | 固定发布目录、在线更新清单、下载转发和媒体范围请求。 |
| `MEDIA.json` | 已审核媒体的来源、尺寸与 SHA-256。 |
| `wrangler.jsonc` | Cloudflare 部署配置，不保存凭据。 |

设计约定见 [DESIGN.md](DESIGN.md)。官网界面调整不代表桌面安装包已经更新。

## 本地验证

从仓库根目录运行：

```powershell
node --test website/*.test.mjs
python scripts/repo_guard.py --all
npx wrangler@4.127.1 dev --config website/wrangler.jsonc
```

检查桌面和手机、浅深主题、键盘操作、无脚本、旧章节链接、历史版本展开和校验复制。截图要来自实际浏览器；产品图片保留原构建标识，不能冒充最新安装包。

## 发布与不变约束

按[发布规程](../docs/RELEASING.md)从已审查 main 部署。先核对账户与域名，再确认对应 GitHub Release 已公开；官网部署使用 `website/wrangler.jsonc`。本地验证不能替代上线检查。

- 下载与更新清单的文件名、标签、来源提交、大小和 SHA-256 必须对应同一资产。已发布标签和安装包不替换；历史下载路由继续有效。
- 安装包由 GitHub Releases 托管，Worker 流式转发并支持断点续传，不把二进制塞进静态目录；保留 GitHub 备用链接。
- 上线后以未登录请求完整下载安装包并校验摘要，再检查更新清单、媒体播放和范围请求。只检查首页返回成功不算验收完成。
- `MEDIA.json` 登记的媒体保留原始字节与出处；历史视频保留日期、字幕和播放控件。固定匿名演示不处理真实文件。
- 匿名浏览、下载开始和 GitHub 备用点击保持独立口径；不把下载开始写成安装成功，不把统计失败写成零。
- `public/support.json` 保持关闭；统计页权限、会话、许可和页脚隐私说明保留。凭据只由发布设备自行登录，不进入 Git 或 CI。
