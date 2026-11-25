"""WorkBox 的团队交件工作区。"""

import sys


def uses_team_installer():
    """The bundled desktop and office server must be upgraded as one distribution."""
    return bool(getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None))


TEAM_UPGRADE_MESSAGE = '请向分发人领取新版 WorkBox 团队版安装包。关闭桌面工作箱后运行安装包；办公室服务器升级前需先备份并停止服务。'
