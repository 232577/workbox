import time
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait(controller):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if not controller.busy:
            return
        # 释放 GIL，避免测试等待循环阻塞后台 FileJob 的 Python 文件处理。
        time.sleep(0.015)
    pytest.fail('组合包操作未结束')


def test_user_can_resolve_duplicate_batch_without_updating_wrong_projects(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    for batch in ('第一次批次', '第二次批次'):
        service.capture_message('1. 甲检测平台\n2. 乙调度平台', batch)
    source = tmp_path / '甲检测平台-2-G.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('甲检测平台/说明.txt', '甲')
        archive.writestr('乙调度平台/说明.txt', '乙')
    package = service.import_zip(source)['zip']
    assert service.db.get_zip_bundle(package['id'])['state'] == 'needs-review'
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        panel = controller.panel
        panel.tabs.setCurrentIndex(1)
        panel.zip_list.setCurrentRow(0)
        assert panel.recognize_button.isEnabled()
        def choose_batch():
            for dialog in QApplication.topLevelWidgets():
                if isinstance(dialog, QDialog) and dialog.windowTitle() == '核对多项目 ZIP':
                    combo = dialog.findChild(QComboBox)
                    combo.setCurrentIndex(combo.findData(2))
                    next(b for b in dialog.findChildren(QPushButton) if b.text() == '识别并更新项目').click()
        QTimer.singleShot(60, choose_batch)
        panel.recognize_button.click()
        wait(controller)
        assert service.get_project(1)['status'] == service.get_project(2)['status'] == '待开发'
        assert service.get_project(3)['status'] == service.get_project(4)['status'] == '待交付'
        assert '2 项目合包' in panel.zip_list.item(0).text()
        assert '第二次批次' in service.list_zips()[0]['batch_name']
        assert Path(package['path']).exists() and source.exists()
    finally:
        controller.shutdown()


def test_panel_selected_project_does_not_override_bundle_contents(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.capture_message('1. 甲检测平台\n2. 乙调度平台\n3. 无关项目', '同批次')
    source = tmp_path / '甲检测平台-2-G.zip'
    with ZipFile(source, 'w') as archive:
        archive.writestr('甲检测平台/说明.txt', '甲')
        archive.writestr('乙调度平台/说明.txt', '乙')
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert controller.start()
        controller.monitor_timer.stop()
        controller.show_panel()
        for index in range(controller.panel.project_list.count()):
            if controller.panel.project_list.item(index).data(Qt.UserRole) == 3:
                controller.panel.project_list.setCurrentRow(index)
        controller.receive({'kind': 'files', 'paths': [str(source)]})
        wait(controller)
        service.scan_archives(now=0)
        controller.enqueue('检查合包', lambda **kw: service.scan_archives(now=11, **kw))
        wait(controller)
        assert service.get_project(1)['status'] == service.get_project(2)['status'] == '待交付'
        assert service.get_project(3)['status'] == '待开发'
        assert '2 个项目' in controller.panel.notice.text()
    finally:
        controller.shutdown()
