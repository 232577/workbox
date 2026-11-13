"""ZIP 人工交付的内容锁覆盖提交，并在取消和失败后释放。"""
import os
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

import app
from app import WorkBox


@pytest.fixture
def delivery(tmp_path):
    box = WorkBox(tmp_path / 'anonymous.db')
    box.db.create_capture('匿名原文', '匿名批次', [('匿名交付项目', False)])
    source = tmp_path / '匿名交付项目.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('material.txt', '匿名材料')
    record = box.import_zip(source)['zip']
    assert box.get_project(1)['status'] == '待交付'
    return box, record, source


def mutate(path, replacement, action):
    if action == 'overwrite':
        path.write_bytes(b'anonymous replacement content')
    elif action == 'delete':
        path.unlink()
    else:
        replacement.replace(path)


@pytest.mark.skipif(os.name != 'nt', reason='Windows 文件共享模式提供提交期间的内容保护')
@pytest.mark.parametrize('action', ['overwrite', 'delete', 'replace'])
def test_zip_mutations_are_blocked_until_delivery_commit_returns(delivery, monkeypatch, action):
    box, record, source = delivery
    replacement = source.with_name('replacement.zip')
    replacement.write_bytes(source.read_bytes())
    original = box.db.complete_delivery

    def complete_while_attempting_mutation(*args, **kwargs):
        with pytest.raises(OSError):
            mutate(source, replacement, action)
        original(*args, **kwargs)
        # 原方法已退出真实数据库事务；调用方此时仍应持有内容锁。
        assert box.db.delivery_history()['valid_event_count'] == 1
        with pytest.raises(OSError):
            mutate(source, replacement, action)

    monkeypatch.setattr(box.db, 'complete_delivery', complete_while_attempting_mutation)
    result = box.confirm_delivery(1, record['id'], confirmed=True)
    assert result['status'] == '已交付'
    assert box.db.delivery_history()['valid_event_count'] == 1
    # 确认调用结束后不继续占用文件。
    mutate(source, replacement, action)


@pytest.mark.parametrize('portable', [False, True])
def test_successful_zip_confirmation_preserves_event_identity(delivery, monkeypatch, portable):
    box, record, source = delivery
    if portable:
        # 仅切换应用的分支，不改变 pathlib 和其他模块所在平台。
        monkeypatch.setattr(app, 'os', SimpleNamespace(name='posix'))
    result = box.confirm_delivery(1, record['id'], confirmed=True)
    assert result['status'] == '已交付'
    event, = box.db.delivery_history()['events']
    assert (event['project_id'], event['zip_id'], event['kind']) == (1, record['id'], 'manual-zip')
    assert Path(event['archive_path']) == source


def test_cancellation_during_zip_read_releases_file_without_delivery(delivery):
    box, record, source = delivery
    cancel_event = Event()

    def cancel_read(read, _total):
        assert read > 0
        cancel_event.set()

    with pytest.raises(CancelledError):
        box.confirm_delivery(1, record['id'], confirmed=True,
                             cancel_event=cancel_event, progress=cancel_read)
    assert box.get_project(1)['status'] == '待交付'
    assert box.db.delivery_history()['valid_event_count'] == 0
    source.unlink()


def test_already_cancelled_confirmation_does_not_open_missing_zip(delivery):
    box, record, source = delivery
    source.unlink()
    cancel_event = Event()
    cancel_event.set()
    with pytest.raises(CancelledError):
        box.confirm_delivery(1, record['id'], confirmed=True, cancel_event=cancel_event)
    assert box.get_project(1)['status'] == '待交付'
    assert box.db.delivery_history()['valid_event_count'] == 0


def test_changed_zip_is_rejected_and_released_without_delivery(delivery):
    box, record, source = delivery
    with ZipFile(source, 'w') as archive:
        archive.writestr('material.txt', '匿名修改后的材料')
    with pytest.raises(ValueError, match='内容已变化'):
        box.confirm_delivery(1, record['id'], confirmed=True)
    assert box.get_project(1)['status'] == '待交付'
    assert box.db.delivery_history()['valid_event_count'] == 0
    source.unlink()


def test_database_failure_rolls_back_history_and_releases_zip(delivery, monkeypatch):
    box, record, source = delivery
    original_append = box.db._append_delivery_event

    def fail_after_event(*args, **kwargs):
        original_append(*args, **kwargs)
        raise RuntimeError('匿名提交失败')

    monkeypatch.setattr(box.db, '_append_delivery_event', fail_after_event)
    with pytest.raises(RuntimeError, match='匿名提交失败'):
        box.confirm_delivery(1, record['id'], confirmed=True)
    assert box.get_project(1)['status'] == '待交付'
    assert box.db.delivery_history()['valid_event_count'] == 0
    source.unlink()
