# 无窗口安装交接修正（2026-11-03 r2）

真实旧安装版曾在检查、下载校验、数据库恢复点成功后退出，但安装助手没有执行。隔离旧冻结程序也复现了这一状态；窗口不可见的 Python 父进程使用 DETACHED_PROCESS 启动 PowerShell 会出现退出码0却未执行脚本的情况。修正为 CREATE_NO_WINDOW，并保留独立进程组、显式空标准句柄、原目录锁定和摘要校验；辅助进程先记录 helper-started，方便区分“尚未启动”与“正在等待原程序退出”。重启时使用独立 PyInstaller 环境，不继承旧进程的工作子进程身份。

回归实际启动 pythonw 与只写匿名标记的 PowerShell，验证父进程退出后脚本确实执行，不仅检查 Popen 参数或退出码。未降低执行策略、Internet来源标记或Windows安全提示。更早有交接缺陷的安装版可能需要一次明确的辅助安装；不得将这种接续报告为旧版本完全自主完成在线升级。已发布的20261103.1资产保持不可变，修正版使用新标识20261103.2。

参考：PyInstaller 官方 Common Issues and Pitfalls 的独立应用重启环境说明：https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart 。DLL搜索路径另做了无害对照，并未复现本次故障，不将它误报为已经确认的原因。
