"""复制进程中断留下的碎片不能成为工作文档或交付材料。"""

import json
import subprocess
import sys
import threading
from concurrent.futures import CancelledError
from pathlib import Path

import pytest
from PySide6.QtCore import QLockFile

from desktop_service import DesktopService
from project_documents import list_project_documents
from workspace_manager import copy_verified


@pytest.mark.parametrize('existing_document', [False, True])
def test_crashed_copy_is_not_a_document_or_deliverable_material(tmp_path, existing_document):
    service = DesktopService(tmp_path / 'data' / 'box.db')
    pid = service.db.create_capture('匿名系统', '匿名批次', [('匿名系统', False)])['project_ids'][0]
    service.ensure_workspaces(pid)
    root = Path(service.workspaces.get_workspace(pid)['path'])
    source = tmp_path / 'incoming' / '匿名系统说明书.docx'
    source.parent.mkdir()
    initial_content = b'original complete document'
    if existing_document:
        source.write_bytes(initial_content)
        service.receive_file(source, pid, manual=True)
    incoming_content = b'A' * (3 * 1024 * 1024)
    source.write_bytes(incoming_content)
    script = '''
import os, sys
from desktop_service import DesktopService
service = DesktopService(sys.argv[1])
def interrupted(done, total):
    os._exit(95)
service.receive_file(sys.argv[2], int(sys.argv[3]), manual=True, progress=interrupted)
'''
    process = subprocess.run([sys.executable, '-c', script, str(service.db.path), str(source), str(pid)],
                             cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert process.returncode == 95, process.stderr.decode(errors='replace')
    partials = list(root.glob('*.partial'))
    assert len(partials) == 1
    partial = partials[0]
    assert 0 < partial.stat().st_size < len(incoming_content)
    partial_content = partial.read_bytes()

    restarted = DesktopService(service.db.path)
    # 正式启动先取得单实例锁，再恢复；QLockFile 会识别已结束的子进程。
    session = QLockFile(str(service.db.path) + '.session.lock')
    session.setStaleLockTime(0)
    assert session.tryLock(0)
    try:
        assert restarted.recover_project_changes() == []
        expected = [str(root / source.name)] if existing_document else []
        documents = list_project_documents(restarted, pid)
        assert documents['errors'] == []
        assert [row['path'] for row in documents['documents']] == expected
        if existing_document:
            delivered = restarted.confirm_delivery(pid, confirmed=True)
            with restarted.db.connect() as connection:
                manifest = json.loads(connection.execute(
                    'SELECT manifest FROM project_delivery_archives WHERE id=?',
                    (delivered['archive']['id'],)).fetchone()[0])
            assert [row['path'] for row in manifest] == [source.name]
            assert (Path(delivered['archive']['path']) / source.name).read_bytes() == initial_content
        else:
            with pytest.raises(ValueError, match='没有可交付文件'):
                restarted.confirm_delivery(pid, confirmed=True)
            assert restarted.get_project(pid)['status'] != '已交付'
            with restarted.db.connect() as connection:
                assert connection.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0] == 0
        # 保留无法自动归属的中断现场；重试仍应完整发布标准文件名。
        assert partial.read_bytes() == partial_content
        received = restarted.receive_file(source, pid, manual=True)
        assert received['file']['path'] == str(root / source.name)
        assert (root / source.name).read_bytes() == source.read_bytes() == incoming_content
        assert partial.read_bytes() == partial_content
        documents = list_project_documents(restarted, pid)['documents']
        assert [row['path'] for row in documents if row['kind'] == '工作文件'] == [str(root / source.name)]
        assert str(partial) not in [row['path'] for row in documents]
    finally:
        session.unlock()


@pytest.mark.parametrize('failure', ['cancel', 'copy-error'])
def test_handled_copy_failure_cleans_only_its_temporary_file(tmp_path, failure):
    source = tmp_path / 'source.bin'
    source.write_bytes(b'A' * (2 * 1024 * 1024))
    target = tmp_path / 'out' / 'copy.bin'
    target.parent.mkdir()
    retained = target.parent / 'owner.partial'
    retained.write_bytes(b'owner material')
    event = threading.Event()

    def interrupt(done, total):
        if failure == 'cancel':
            event.set()
        else:
            raise OSError('injected copy error')

    with pytest.raises(CancelledError if failure == 'cancel' else OSError):
        copy_verified(source, target, cancel_event=event, progress=interrupt)
    assert source.read_bytes() == b'A' * (2 * 1024 * 1024)
    assert list(target.parent.iterdir()) == [retained]
    assert retained.read_bytes() == b'owner material'
