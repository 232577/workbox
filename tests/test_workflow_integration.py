from pathlib import Path
from zipfile import ZipFile
import time

import pytest
from PySide6.QtCore import QMimeData, QUrl

from desktop_service import DesktopService
from floating import decode_mime
from parser import ParsedProject


def package(path, text='version one'):
    with ZipFile(path, 'w') as archive:
        archive.writestr('readme.txt', text)
    return path


def test_confirmed_message_creates_nested_project_folders_and_preserves_duplicate(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    result = service.receive_text('1. 甲工具\n2. 乙工具\n3. 丙工具')
    root = tmp_path / 'data/工作项目/甲工具'
    assert (root / '甲工具').is_dir(), '消息收录后尚未自动创建工作目录'
    assert (root / '乙工具').is_dir() and (root / '丙工具').is_dir()
    assert len(result['workspaces']) == 3
    assert service.receive_text('1. 甲工具\n2. 乙工具\n3. 丙工具')['duplicate']
    assert len(list((tmp_path / 'data/工作项目').iterdir())) == 1


def test_review_creates_folders_only_after_names_are_confirmed(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    assert hasattr(service, 'workspaces'), '尚未接入工作目录服务'
    row = service.receive_text('这些描述需要核对。')
    assert service.workspaces.list_workspaces() == []
    service.resolve_text(row['id'], [ParsedProject('甲工具'), ParsedProject('乙工具')])
    assert len(service.workspaces.list_workspaces()) == 2


def test_folder_failure_keeps_message_and_exposes_retry(tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data/workbox.db')
    assert hasattr(service, 'workspaces'), '缺少目录失败与重试流程'
    original = service.workspaces.ensure_projects
    def fail(*_):
        raise PermissionError('目录无写入权限')
    monkeypatch.setattr(service.workspaces, 'ensure_projects', fail)
    result = service.receive_text('甲工具')
    assert result['state'] == 'committed' and result['workspace_error']
    assert service.db.counts()['projects'] == 1
    monkeypatch.setattr(service.workspaces, 'ensure_projects', original)
    assert Path(service.ensure_workspaces(1)[0]['path']).is_dir()


def test_regular_files_are_accepted_and_associated_with_visible_project(tmp_path):
    source = tmp_path / '需求文档.txt'
    source.write_text('保留原件', encoding='utf-8')
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    assert decode_mime(mime)['kind'] == 'files', '悬浮入口仍仅支持ZIP'
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('甲工具')
    result = service.receive_file(source, project_id=1)
    assert source.exists() and Path(result['file']['path']).read_text(encoding='utf-8') == '保留原件'
    assert service.file_candidates(result['file']['path'])[0]['id'] == 1


def test_dragged_zip_waits_stable_then_copies_and_keeps_accepted_previous_version(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    assert hasattr(service, 'snapshots'), '缺少持续ZIP归档服务'
    service.receive_text('甲工具')
    source = package(Path(service.get_project(1)['workspace']['path']) / '甲工具.zip')
    result = service.receive_file(source, project_id=1)
    assert result['watching'] and source.exists() and service.list_zips() == []
    start = time.time()
    service.scan_archives(now=start)
    archived = service.scan_archives(now=start+11)['archived']
    assert len(archived) == 1 and source.exists()
    old_id = archived[0]['id']
    service.snapshots.approve_version(old_id)
    package(source, 'version two')
    service.scan_archives(now=start+12)
    assert len(service.scan_archives(now=start+23)['archived']) == 1
    versions = service.snapshots.list_versions(1)
    assert len(versions) == 2
    assert [v['zip_id'] for v in versions if v['approved']] == [old_id]
    assert service.get_project(1)['status'] == '待交付'


def test_explicit_import_preserves_source_instead_of_legacy_moving(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('甲工具')
    source = package(tmp_path / '甲工具.zip')
    result = service.import_zip(source)
    assert source.exists(), '新收件仍在移动原ZIP'
    assert Path(result['zip']['path']).is_relative_to(tmp_path / 'data/ZIP归档')


def test_workflow_controller_has_background_monitor_and_new_controls(tmp_path):
    from PySide6.QtWidgets import QApplication
    from desktop_controller import FloatingController
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data/workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        assert hasattr(controller, 'monitor_timer'), '未连接自动归档计时器'
        assert controller.monitor_timer.isActive()
        assert hasattr(controller.panel, 'project_context_menu')
        assert not hasattr(controller.panel, 'project_operations')
        assert hasattr(controller.panel, 'settings_button')
        assert hasattr(controller.panel, 'approve_button')
    finally:
        controller.shutdown()
