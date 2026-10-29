"""首次设置进度与当前工作模式；不改写业务数据或已有偏好。"""

from __future__ import annotations

import sqlite3


MODE_LABELS = {'manual': '通用工作模式', 'automatic': '自动交付模式'}

_WORKSPACE_TABLES = frozenset({
    'workflow_settings', 'workflow_archive_roots', 'project_workspaces', 'work_files',
})
_HISTORY_TABLES = (
    'projects', 'batches', 'messages', 'zips', 'file_intake_events',
    'delivery_events', 'delivery_event_actions', 'project_delivery_history_markers',
    'project_delivery_count_actions', 'project_types', 'project_type_links',
)
_STATE_ERROR = '首次设置记录缺失或损坏，未自动重置；请保留数据库并检查。'


def mode_key(settings):
    """以真实的自动交付开关显示模式，自动建项可独立调整。"""
    automatic = settings['auto_deliver_zip']
    if type(automatic) is not bool:
        raise ValueError('ZIP 自动交付设置必须是布尔值。')
    return 'automatic' if automatic else 'manual'


def setup_required(connection):
    """读取唯一进度记录；缺失或损坏时不能猜测用户是否已完成。"""
    try:
        rows = connection.execute('SELECT id,completed FROM workflow_first_run').fetchall()
    except sqlite3.DatabaseError as error:
        raise RuntimeError(_STATE_ERROR) from error
    if (len(rows) != 1 or rows[0]['id'] != 1
            or type(rows[0]['completed']) is not int or rows[0]['completed'] not in (0, 1)):
        raise RuntimeError(_STATE_ERROR)
    return rows[0]['completed'] == 0


def initialize(connection):
    """必须在工作目录表创建前调用，区分空白核心库与已有安装。"""
    connection.execute('BEGIN IMMEDIATE')
    tables = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'workflow_first_run' in tables:
        setup_required(connection)
        return
    legacy = bool(tables & _WORKSPACE_TABLES)
    if not legacy:
        legacy = any(
            table in tables and connection.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None
            for table in _HISTORY_TABLES
        )
    connection.execute('''CREATE TABLE workflow_first_run (
        id INTEGER PRIMARY KEY CHECK(id=1),
        completed INTEGER NOT NULL CHECK(typeof(completed)='integer' AND completed IN (0,1))
    )''')
    connection.execute('INSERT INTO workflow_first_run(id,completed) VALUES (1,?)', (int(legacy),))


def complete(connection):
    """由整个配置事务调用，写入失败时设置与完成标记一起回滚。"""
    if setup_required(connection):
        connection.execute('UPDATE workflow_first_run SET completed=1 WHERE id=1')
