# WorkBox 工作箱

Windows 上的个人项目工作箱：收材料、找文件、核对交付。数据保存在本机，无需账号。

[下载 Windows 安装版](https://workbox.ltxiangzi.dpdns.org/#download) · [使用指南](https://workbox.ltxiangzi.dpdns.org/guide.html) · [更新记录](https://workbox.ltxiangzi.dpdns.org/releases.html)

![WorkBox 项目工作台，匿名示例](docs/images/project-table-export-20261112/workbench-light.png)

*项目表导出功能的匿名源码界面，摄于 2026-11-12；安装包内容以对应发布记录为准。*

- **收好材料**：拖入消息、文件或文件夹，复制校验后归入项目，保留原件。
- **找到当前文件**：按状态、类型、日期和批次筛选；旧稿单独保留。
- **核对交付**：查看整批进度与交付记录，导出筛选结果或勾选项目。

## 安装与更新

支持 Windows 10 1809+ / Windows 11 x64，自带运行环境，未做商业代码签名。当前版本 **1.0.0**，正式构建 **2026-11-13 r2**。

升级前备份 data、工作目录、归档目录及全部已登记历史文稿目录，再从“高级设置 → 检查更新”操作。[安装指南](https://workbox.ltxiangzi.dpdns.org/guide.html#installation) · [更新恢复](docs/UPDATE-RECOVERY.md)

<details>
<summary>当前安装包与校验信息</summary>

[GitHub 备用下载](https://github.com/232577/workbox/releases/tag/v1.0.0.20261113.2)

- 文件：`WorkBox-1.0.0-Setup-x64-20261113-r2.exe`
- 大小：40,841,667 字节
- SHA-256：`dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314`
- 来源提交：`ff4a336d66adb65c6cfedfb5b258067c703dee69`
- 验收范围以该 Release 的 `PUBLIC_BUILD_INFO.json` 为准。

</details>

<details>
<summary>参与开发</summary>

技术栈为 Python 3.11、PySide6、SQLite 和 PyInstaller。Git 只同步代码，不同步个人数据和材料；正式构建来自审查通过的 main。

先读[协作指南](CONTRIBUTING.md)、[项目约定](AGENTS.md)和[接手地图](docs/PROJECT-TAKEOVER-2026-11-12.md)。安装依赖和分支流程见协作指南，构建与上线见[发布规程](docs/RELEASING.md)。

```powershell
.\.venv\Scripts\python.exe main.py --panel
.\.venv\Scripts\python.exe -m scripts.run_tests -q
node --test website/*.test.mjs
```

[验证范围](VERIFICATION.md) · [逐版说明](RELEASE_NOTES.md) · [历史公开说明存档](docs/releases/RELEASE-NOTES-ARCHIVE.md)

</details>

软件不读取聊天、不监听剪贴板、不上传项目数据。[GPL-3.0-only 许可](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md)
