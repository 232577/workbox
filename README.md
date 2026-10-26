# WorkBox 工作箱

WorkBox 是 Windows 本地项目与交付文件管理工具。通过悬浮球打开管理面板，收录项目、整理工作目录并保存交付归档。项目状态包括待开发、开发中、待交付和已交付，紧急标记独立管理。

## 下载与安装

在 [工作箱下载站](https://workbox.ltxiangzi.dpdns.org/) 或 [GitHub Releases](https://github.com/232577/workbox/releases) 下载 `WorkBox-1.0.0-Setup-x64.exe`，下载和使用均无需登录。安装包适用于 Windows 10 1809 及以上版本、Windows 11 x64。当前安装包没有商业数字签名。

安装包不包含原电脑的项目、数据库和交付记录。首次启动建立空白数据目录；数据保存在安装目录的 `data` 文件夹。升级前请退出程序并备份数据。卸载保留运行后创建的数据。下载后可用 PowerShell 核对摘要：

```powershell
Get-FileHash .\WorkBox-1.0.0-Setup-x64.exe -Algorithm SHA256
```

1.0.0 安装包 SHA256：`ad5025a6ca868609cbff621ed2ee6225c635626d50a0e5b25eda357695a859d8`。

## 日常使用

- 把项目文字拖入悬浮球，或使用粘贴、文件选择入口收录。
- 在项目行右键管理状态、紧急标记、备注、截止日期及归档。
- 普通文件收录后进入待交付；ZIP 按项目分别核实文件数量，符合规则后标记已交付。归档复制文件并保留原件。
- 今日交付按每个项目的首次有效交付统计。撤销误记会把项目退回待交付；恢复记录仅恢复统计效力。
- “后台运行”保留监视与托盘入口；“退出”关闭程序。

程序使用本地 SQLite 和文件目录，不提供账号、云同步或远程数据库服务。请自行备份数据与归档文件。

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
