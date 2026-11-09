from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from delivery_ui import TodayDeliveryDialog
from desktop_service import DesktopService
from project_documents_ui import ProjectDocumentsPane
from theme import install_theme, tokens


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_theme()
    return app


def test_file_actions_follow_selection_and_size_aligns_with_header(qapp):
    pane = ProjectDocumentsPane(SimpleNamespace())
    pane.shutdown()
    pane.project_id = 1
    pane.resize(470, 282)
    pane.show()
    document = dict(id='file-1', name='操作说明.txt', relative_path='操作说明.txt',
                    path='fixture/操作说明.txt', kind='工作文件', exists=True,
                    size=1024, modified_at=None, zip_id=None, is_directory=False)
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[], documents=[document]))
    qapp.processEvents()
    assert not pane.open_button.isVisible()
    assert pane.file_menu_button.isVisible()
    assert not pane.folder_action.isEnabled()
    assert pane.table.horizontalHeaderItem(2).textAlignment() & Qt.AlignRight
    assert pane.table.item(0, 2).textAlignment() & Qt.AlignRight

    pane.table.selectRow(0)
    assert pane.open_button.isVisible() and pane.open_button.isEnabled()
    assert pane.folder_action.isEnabled()
    pane.apply_result(dict(project_id=1, workspace_path='fixture', errors=[],
                           documents=[dict(document, exists=False, size=None)]))
    assert pane.open_button.isVisible() and not pane.open_button.isEnabled()
    assert pane.folder_action.isEnabled()
    frame = pane.table.viewport().grab().toImage()
    scale = frame.devicePixelRatio()
    rect = pane.table.visualItemRect(pane.table.item(0, 1))
    error = QColor(tokens()['error']).getRgb()[:3]

    def is_error_pixel(x, y):
        # Windows 中文字体的抗锯齿会混合相邻颜色，像素仍应接近实际错误色。
        rgb = frame.pixelColor(x, y).getRgb()[:3]
        return all(abs(actual - expected) <= 5 for actual, expected in zip(rgb, error))

    error_pixels = sum(is_error_pixel(x, y)
                       for x in range(round(rect.left() * scale), round(rect.right() * scale))
                       for y in range(round(rect.top() * scale), round(rect.bottom() * scale)))
    assert error_pixels > 5
    pane.table.clearSelection()
    assert not pane.open_button.isVisible()
    pane.hide()


def test_delivery_sections_name_visible_records_once_at_each_width(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 匿名交付项目')
    with service.db.connect() as connection:
        connection.execute('''INSERT INTO delivery_events
            (project_id,project_name,batch_name,kind,archive_path,occurred_at)
            VALUES (1,'匿名交付项目','隔离批次','manual-files','','2026-11-06T03:00:00Z')''')
    page = TodayDeliveryDialog(service, embedded=True)
    page.resize(980, 680)
    page.show()
    page.all_dates.setChecked(True)
    page.table.selectRow(0)
    qapp.processEvents()
    assert page.project_heading.isVisible() and page.project_heading.text() == '项目记录 · 1'
    assert page.detail_heading.isVisible() and page.detail_heading.text() == '交付明细 · 1'

    page.resize(660, 480)
    page.compact_tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert not page.project_heading.isVisible() and not page.detail_heading.isVisible()
    assert page.compact_tabs.tabText(1) == '交付明细（1）'
    assert page.details.currentRow() == 0
    page.shutdown()
    page.hide()
