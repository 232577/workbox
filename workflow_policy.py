"""每台电脑本地保存的收件和 ZIP 交付规则。"""

from __future__ import annotations

import json


DEFAULTS = dict(auto_create_projects=True, auto_deliver_zip=True, zip_delivery_min_files=4)


def validate(values):
    for key in ('auto_create_projects', 'auto_deliver_zip'):
        if type(values[key]) is not bool:
            raise ValueError('自动建项与自动交付设置必须是布尔值。')
    minimum = values['zip_delivery_min_files']
    if type(minimum) is not int or not 1 <= minimum <= 10000:
        raise ValueError('ZIP 自动交付文件门槛应为 1 至 10000 个。')
    return values


def read_policy(connection):
    values = dict(DEFAULTS)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_settings'").fetchone():
        for row in connection.execute('SELECT key,value FROM workflow_settings'):
            if row['key'] in values:
                values[row['key']] = json.loads(row['value'])
    return validate(values)


def creation_disabled_message(has_projects):
    return ('自动建项已关闭，请选择已有项目或先创建项目。' if has_projects
            else '自动建项已关闭，请先创建项目，再收录文件。')
