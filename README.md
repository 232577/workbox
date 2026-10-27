# WorkBox 工作箱

WorkBox 是 Windows 本地项目与交付文件管理工具。通过悬浮球打开管理面板，收录项目、整理工作目录并保存交付归档。项目状态包括待开发、开发中、待交付和已交付，紧急标记独立管理。

## 下载与安装

在 [工作箱下载站](https://workbox.ltxiangzi.dpdns.org/) 或 [增强版 Release](https://github.com/232577/workbox/releases/tag/v1.0.0-enhanced.20261027) 下载 `WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe`，下载和使用均无需登录。本次为 2026-10-27 增强构建，软件版本保持 1.0.0。安装包适用于 Windows 10 1809 及以上版本、Windows 11 x64，没有商业数字签名。[2026-10-26 旧版](https://github.com/232577/workbox/releases/tag/v1.0.0) 保留。

安装包不包含原电脑的项目、数据库和交付记录。首次启动建立空白数据目录；数据保存在安装目录的 `data` 文件夹。升级前请退出程序并备份数据。卸载保留运行后创建的数据。下载后可用 PowerShell 核对摘要：

```powershell
Get-FileHash .\WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe -Algorithm SHA256
```

增强版安装包 SHA256：`bcd0724ac1fc9dc311d08205034e4995aa32889c6c60798315425b13fbae0c2a`。

## 日常使用

- 把项目文字拖入悬浮球，或使用粘贴、文件选择入口收录。
- 支持可访问的 Windows 共享文件来源，以及整个文件夹收录。保留子目录、原件与已成功项，歧义材料需要明确选择。
- 在项目行右键管理状态、紧急标记、备注、截止日期及归档。
- 项目右键“删除项目…”移入已删除列表，可恢复，文件、归档和交付历史保留。
- 普通文件收录后进入待交付；ZIP 按项目分别核实文件数量，符合规则后标记已交付。归档复制文件并保留原件。
- 今日交付按每个项目的首次有效交付统计。撤销误记会把项目退回待交付；恢复记录仅恢复统计效力。
- “后台运行”保留监视与托盘入口；“退出”关闭程序。

程序使用本地 SQLite 和文件目录，不提供账号、云同步或远程数据库服务。请自行备份数据与归档文件。

应用、托盘、悬浮球与官网使用同源折叠盒/W矢量标志，生成源为 `branding.py`。
下载站的浏览与下载统计由独立网站服务记录，仅站点所有者登录可见，不收集桌面项目数据。

## 从源码运行

建议使用 Windows x64 与 Python 3.11。技术栈为 Python、PySide6、SQLite 和 PyInstaller。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe main.py --panel
```

运行公开测试及生成冻结程序：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

构建结果位于 `dist/WorkBox`。测试使用隔离夹具，公开测试中的项目名称已匿名化。源码不包含原工作区的操作日志、历史截图或用户数据。

制作安装包需要另行安装 Inno Setup 6。生成安装载荷前，先核对冻结程序摘要；使用 `python -m scripts.build_installer --help` 查看输入参数。安装脚本按当前用户安装，保留运行后创建的数据。

## 源码与第三方组件

源码公开于 [232577/workbox](https://github.com/232577/workbox)，目前尚未选择 WorkBox 自有代码的开源许可证。

第三方组件遵循各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 `licenses/`。Qt 动态库单独存放于冻结程序的 `_internal` 中，第三方说明包含对应上游源码链接及替换说明。
