"""WorkBox 的团队交件工作区。"""

import sys


def uses_team_installer():
    """Older frozen distributions require a manual installation."""
    return bool(getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None)
                and getattr(sys, '_workbox_distribution_role', None) != 'workstation')


TEAM_UPGRADE_MESSAGE = ('请从发布页获取团队版安装包，退出 WorkBox 后在原程序目录安装。'
                        '工作站包仅更新桌面。办公室服务器请由管理员另行备份、停服并更新。')
