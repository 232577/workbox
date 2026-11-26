# WorkBox 工作箱

WorkBox 是 Windows 上的项目工作箱。个人项目、材料和交付记录保存在本机，个人工作台无需账号。使用团队交件时，在工作箱内登录办公室服务和原站。

[下载 Windows 安装版](https://github.com/232577/workbox/releases/latest) · [使用指南](https://workbox.ltxiangzi.dpdns.org/guide.html) · [更新记录](https://workbox.ltxiangzi.dpdns.org/releases.html)

![WorkBox 项目工作台，匿名示例](docs/images/project-table-export-20261112/workbench-light.png)

*项目表导出功能的匿名源码界面，摄于 2026-11-12；安装包内容以对应发布记录为准。*

- **收好材料**：拖入消息、文件或文件夹，复制校验后归入项目，保留原件。
- **找到当前文件**：按状态、类型、日期和批次筛选；旧稿单独保留。
- **核对交付**。查看个人项目的进度，导出交付记录。使用团队交件时，拖入按项目全名命名的手册，工具箱核实代码人员可取回同一份 Word 后，再提交原站材料状态。[团队交件使用说明](docs/TEAM-DELIVERY.md)

## 安装与更新

支持 Windows 10 1809+ / Windows 11 x64，自带运行环境，未做商业代码签名。产品版本 **1.0.0**。各正式构建的安装包、来源及校验值见 [GitHub Releases](https://github.com/232577/workbox/releases/latest)。

升级前备份 data、工作目录、归档目录及全部已登记历史文稿目录。旧个人版可在“高级设置 → 检查更新”里升级。团队试用版和 2026-11-25 r2 需先退出工作箱，再运行新版工作站完整安装包，选原安装目录。办公室服务器由管理员单独升级。[安装指南](https://workbox.ltxiangzi.dpdns.org/guide.html#installation) · [更新恢复](docs/UPDATE-RECOVERY.md)

<details>
<summary>2026-11-25 r2 安装包与校验信息</summary>

[GitHub 备用下载](https://github.com/232577/workbox/releases/tag/v1.0.0.20261125.2)

- 完整安装包 `WorkBox-1.0.0-Setup-x64-20261125-r2.exe`，135,264,533 字节。本次更新使用完整安装包。
- SHA-256 `04d5a420c363a8ad19f028ab21e86596c2e7088fe0183a8e952164ee9c21463b`
- 来源提交 `d337659261024ad3634154f75da26bcb49ac5a2c`
- 来源及载荷校验信息见该 Release 的 `BUILD_PROVENANCE.json` 与 `PACKAGE_CONTENTS.sha256`。构建验证范围见发布说明。

</details>

<details>
<summary>参与开发</summary>

技术栈为 Python 3.11、PySide6、SQLite 和 PyInstaller。Git 只同步代码，不同步个人数据和材料；正式构建来自审查通过的 main。

桌面源码位于 [`workbox/`](workbox/)。按职责查看[代码地图](docs/PROJECT-TAKEOVER-2026-11-12.md#代码地图)，按[协作指南](CONTRIBUTING.md)和[项目约定](AGENTS.md)开展开发。构建使用 [`scripts/build.ps1`](scripts/build.ps1)，见[发布规程](docs/RELEASING.md)。

```powershell
.\.venv\Scripts\python.exe -m workbox --panel
.\.venv\Scripts\python.exe -m scripts.run_tests -q
node --test website/*.test.mjs
```

[验证范围](VERIFICATION.md) · [逐版说明](RELEASE_NOTES.md) · [历史公开说明存档](docs/releases/RELEASE-NOTES-ARCHIVE.md)

</details>

个人工作台在本机归档。团队交件会将你选定的文件发送到已配置的办公室服务。原站只接收对应任务的材料状态变更。软件不读取聊天或监听剪贴板。[GPL-3.0-only 许可](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md)
