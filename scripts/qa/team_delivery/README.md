# 团队交件 Windows 验收

在交互式 Windows 桌面运行。桌面源码需已集成 `QtSiteSession(origin, parent)`、`QtSiteGateway(session)` 和现有 Qt `OfficePanel`。原站入口使用产品创建的独立 Qt 窗口，登录、登出和“只看我的”均由 Qt 窗口接收鼠标和键盘事件。Playwright 供匿名办公室后台、代码人员和提交人员使用。

准备桌面 Python 依赖、Playwright、pypdf、可启动的 Chromium、包含匿名浏览器夹具的 WorkBox Online 测试源码和已解包的 Kanboard v11 程序。Online 验收模块需提供 `browser_test(..., desktop_window=...)`。已有独立依赖环境时，可传入 `--playwright-packages` 和 `--pdf-packages`。

独立桌面启动器需要 pywin32。它新建独立 Windows 桌面，保留其中的原生窗口显示，并把 `--` 后的参数传给验收脚本。每轮使用新的日志文件。

```powershell
python -B scripts/qa/team_delivery/run_native.py --log .local/team-delivery-qa/native.log -- `
  --desktop-source <已集成的干净桌面源码目录> `
  --expected-desktop-commit <集成候选完整提交号> `
  --online-source <Online 测试源码目录> `
  --expected-online-commit 8b135d9c9d675d9520e88e1a59ca50396c2203c7 `
  --program <已解包的 Kanboard 程序目录> `
  --expected-server-commit 8cf3cd97dafe85675ba6e9cda7417f90c962d64c `
  --expected-server-bundle-sha256 b55569cb672a1fa5654cc27831949da6d8333e3d69187056db0807612df767bd `
  --chromium <Chromium EXE> `
  --site-assets <原站公开前端离线副本目录> `
  --output .local/team-delivery-qa
```

原站和办公室分别使用新建的本机回环地址及独立的临时 Qt 浏览器配置。验收打开 WorkBox 原有主窗口，核对后台新增和改派、同名账号切换、同名项目编号选择、所选编号在确认前被改派、本人拖入一份 Word、原站单项提交及响应超时后读回。最终核对已完成任务从本人待办移出、重复拖入只读、代码角色下载的 Word 字节与归档原件一致、提交角色取得五件包以及首次手册、首次代码和首次项目工作量各记一次。

独立职责验收后，另建一个匿名兼任账号进入代码和提交页面，核对两项职责可同时使用，既有工作量和登记内容保持原样。

原站离线目录包含 `public-index.html`、`public-app.js`、`app.css`、`dashboard.js`、`excel-export.js` 和 `notifications.js`。脚本按原字节提供素材，并在 `results.json` 记录文件摘要。模拟原站固定使用匿名任务 55，办公室项目编号来自本次匿名服务器的实际分发。

`desktop-observations.json` 记录 Qt 场景进度和原站匿名请求，截图记录可见状态。`results.json` 记录验收结果、源码提交号、下载摘要、两个 PDF 的中文提取以及工作量。`lifecycle.json` 记录到期清理、端口与进程的关闭情况。联合检查全部完成且本轮临时桌面状态已清理时，`acceptance_complete` 为 `true`。

可单独运行匿名 HTTP 夹具测试。

```powershell
python -B -m pytest scripts/qa/team_delivery/test_original_frontend.py -q
```

用 `--require-scratch-pruned` 核对服务器仍运行时，已成功发布的转换临时目录是否已经清空。验收自身的离线清理另记在 `lifecycle.json`。

## 冻结程序冒烟

对候选程序目录中的 `WorkBox.exe` 运行下列命令。脚本通过 Windows UI 自动化操作首次设置、团队交件、项目和退出，核对团队页在同一主窗口中显示。此检查只覆盖程序启动、原生按钮和正常退出，双账号和文件生产由上面的源码整链验收覆盖。

```powershell
pwsh -NoProfile -File scripts/qa/team_delivery/verify_frozen.ps1 `
  -Executable <候选程序目录>/WorkBox.exe `
  -Output .local/team-delivery-qa
```

证据输出应放在 Git 忽略的 `.local` 或源码仓库外。验收运行无需个人数据库、原站账号或生产服务。
