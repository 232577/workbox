"""以隔离工作台核对批次余量和原有清单交互。"""
from os import environ
from pathlib import Path
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel, ProjectList, populate
from desktop_service import DesktopService
from fluent_views import (PROJECT_BATCH_MARKERS_ROLE, _batch_badge_layout,
                          _batch_badge_parts, batch_progress_summary)
from theme import set_theme


@pytest.fixture(scope='module')
def qt_app():
    app = QApplication.instance() or QApplication([])
    # Windows 的 offscreen 插件可能没有发现系统字体，截图仍需绘制真实文字。
    fonts = []
    if app.platformName() == 'offscreen' and 'WINDIR' in environ and 'Microsoft YaHei UI' not in QFontDatabase.families():
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
            font_id = QFontDatabase.addApplicationFont(str(Path(environ['WINDIR']) / 'Fonts' / name))
            if font_id >= 0:
                fonts.append(font_id)
    yield app
    for font_id in fonts:
        QFontDatabase.removeApplicationFont(font_id)


def test_long_batch_progress_reserves_space_for_one_number_and_more_count(qt_app):
    font = QFont('Microsoft YaHei UI')
    font.setPixelSize(11)
    metrics = QFontMetrics(font)
    markers = [
        {'label': 'R261109-123456789012345', 'total_count': 15, 'delivered_count': 3, 'remaining_count': 12, 'pending_delivery_count': 0},
        {'label': 'G261109-123', 'total_count': 1, 'delivered_count': 1, 'remaining_count': 0, 'pending_delivery_count': 0},
        {'label': 'B261109-456'},
    ]
    width = 24 + metrics.horizontalAdvance(markers[0]['label'] + ' 待完成12，共15') + 5 + metrics.horizontalAdvance('+2批')
    badges, summary = _batch_badge_layout(markers, metrics, width)
    assert badges == [(markers[0], 24 + metrics.horizontalAdvance(markers[0]['label'] + ' 待完成12，共15'))]
    assert summary == '+2批'
    compact, summary = _batch_badge_layout(markers, metrics, width - 16)
    assert [marker for marker, _ in compact] == markers[:1]
    assert compact[0][1] + 5 + metrics.horizontalAdvance(summary) <= width - 16
    full, summary = _batch_badge_layout(markers[1:], metrics, 500)
    assert [size for _, size in full] == [24 + metrics.horizontalAdvance(text) for text in ('G261109-123 待完成0，共1', 'B261109-456')]
    assert summary == ''


def test_pending_delivery_reduces_work_remaining_without_claiming_delivery(qt_app):
    marker = {'label': 'R261109-1', 'total_count': 4, 'delivered_count': 1,
              'pending_delivery_count': 1, 'remaining_count': 3}
    assert batch_progress_summary(marker) == '待完成2，共4'
    assert _batch_badge_parts(marker) == ('R261109-1', ' 待完成2，共4')
    marker.update(pending_delivery_count=3)
    assert batch_progress_summary(marker) == '待完成0，共4'
    assert _batch_badge_parts(marker) == ('R261109-1', ' 待完成0，共4')
    marker.update(delivered_count=4, pending_delivery_count=0, remaining_count=0)
    assert _batch_badge_parts(marker) == ('R261109-1', ' 待完成0，共4')
    marker.pop('pending_delivery_count')
    assert batch_progress_summary(marker) == ''
    assert _batch_badge_parts(marker) == ('R261109-1', '')


@pytest.mark.parametrize('pending', [-1, 4, True, None, '1'])
def test_invalid_pending_delivery_count_never_reports_completion(qt_app, pending):
    marker = {'label': 'R261109-1', 'total_count': 3, 'delivered_count': 0,
              'pending_delivery_count': pending, 'remaining_count': 3}
    assert batch_progress_summary(marker) == ''
    assert _batch_badge_parts(marker) == ('R261109-1', '')


def test_project_accessibility_retains_known_and_unknown_batch_progress(qt_app):
    view = ProjectList()
    markers = [
        {'label': 'R261109-1', 'code': 'R20261109-1', 'total_count': 3, 'delivered_count': 1, 'remaining_count': 2, 'pending_delivery_count': 1},
        {'label': 'G261109-4', 'code': 'G20261109-4', 'total_count': 4, 'delivered_count': 4, 'remaining_count': 0, 'pending_delivery_count': 0},
        {'label': 'B261109-7', 'code': 'B20261109-7'},
        {'label': 'B261109-8', 'code': 'B20261109-8', 'total_count': 0, 'delivered_count': 0, 'remaining_count': 0, 'pending_delivery_count': 0},
    ]
    populate(view, [{'id': 1, 'name': '匿名项目', 'batch_markers': markers}], lambda row: row['name'])
    item = view.item(0)
    for description in (item.toolTip(), item.data(Qt.AccessibleDescriptionRole)):
        assert all(marker['code'] in description for marker in markers)
        assert '待完成1，共3；待交付1，已交付1' in description
        assert '待完成0，共4；待交付0，已交付4' in description
        assert '待完成0，共0；待交付0，已交付0' in description
        assert description.count('待完成') == 3


def test_all_pending_batch_stays_pending_on_every_status_page(qt_app, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    receipt = service.receive_text('1. 匿名甲系统\n2. 匿名乙系统')
    for project_id in receipt['project_ids']:
        service.set_status(project_id, '待交付')
    panel = CompactPanel(service)
    panel.resize(660, 480)
    panel.show()
    try:
        key = f"receipt:{receipt['receipt_id']}"
        panel.project_filter_button.click()
        qt_app.processEvents()
        panel.batch_filter.setCurrentIndex(panel.batch_filter.findData(key))
        expected = '待完成0，共2'
        for status in ('待开发', '开发中', '待交付', '已交付'):
            panel.status_buttons[status].click()
            qt_app.processEvents()
            assert panel.batch_progress_summary.text() == expected
            assert expected in panel.batch_filter.accessibleDescription()
            assert '待交付2，已交付0' in panel.batch_filter.accessibleDescription()
            assert panel.project_list.count() == (2 if status == '待交付' else 0)
        panel.status_buttons['待交付'].click()
        panel.project_list.setCurrentRow(0)
        selected = panel.project_list.currentItem()
        assert expected in selected.toolTip()
        assert expected in selected.data(Qt.AccessibleDescriptionRole)
        marker, = selected.data(PROJECT_BATCH_MARKERS_ROLE)
        assert _batch_badge_parts(marker)[1] == ' 待完成0，共2'
        assert service.db.delivery_history()['valid_event_count'] == 0
        for mode in ('light', 'dark'):
            set_theme(mode, persist=False)
            panel.refresh()
            qt_app.processEvents()
            assert panel.project_list.currentItem() is selected
            assert panel.batch_progress_summary.isVisible()
            assert panel.batch_progress_summary.height() >= panel.batch_progress_summary.fontMetrics().height()
            assert panel.grab().save(str(tmp_path / f'batch-pending-660-{mode}.png'))
    finally:
        panel.document_pane.shutdown()
        panel.hide()
        set_theme('light', persist=False)


def test_delivery_and_void_refresh_filtered_workbench_in_place(qt_app, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    message = [f'{number}. 匿名检修项目{number:02d}' for number in range(1, 24)]
    message.append('24. 匿名验收工具')
    service.receive_text('\n'.join(message))
    target = next(project for project in service.list_projects() if project['name'] == '匿名验收工具')
    panel = CompactPanel(service)
    panel.resize(660, 480)
    panel.show()
    try:
        qt_app.processEvents()
        assert not panel.batch_progress_summary.isVisible()
        panel.search.setText('匿名检修')
        panel.status_buttons['待开发'].click()
        panel.project_filter_button.click()
        qt_app.processEvents()
        panel.batch_filter.setFocus()
        QTest.keyClick(panel.batch_filter, Qt.Key_Down)
        qt_app.processEvents()
        assert panel.batch_progress_summary.isVisible()
        assert panel.batch_progress_summary.text() == '待完成24，共24'
        batch_key = panel.batch_filter.currentData()
        panel.project_filter_button.click()
        qt_app.processEvents()
        assert not panel.batch_progress_summary.isVisible()
        projects = panel.project_list
        ids = [projects.item(i).data(Qt.UserRole) for i in range(projects.count())]
        assert len(ids) == 23
        projects.setCurrentRow(0)
        selected = projects.currentItem()
        projects.verticalScrollBar().setValue(projects.verticalScrollBar().maximum() // 2)
        anchor = projects.itemAt(QPoint(5, 5))
        assert anchor is not None and anchor is not selected
        offset = projects.visualItemRect(anchor).top()
        splitter_sizes = panel.project_splitter.sizes()

        source = tmp_path / '匿名验收工具.zip'
        with ZipFile(source, 'w') as archive:
            for number in range(4):
                archive.writestr(f'材料/{number}.txt', f'隔离材料 {number}')
        service.snapshots.archive_now(source, target['id'])
        events = service.db.delivery_events_for_project(target['id'])
        assert len(events) == 1 and service.db.get_project(target['id'])['status'] == '已交付'

        for delivered in (1, 0):
            if not delivered:
                service.db.void_delivery_events([events[0]['id']], confirmed=True)
            panel.refresh()
            qt_app.processEvents()
            progress = '待完成23，共24'
            assert panel.batch_progress_summary.text() == progress
            assert progress in panel.batch_filter.accessibleDescription()
            assert f'待交付{1 - delivered}，已交付{delivered}' in panel.batch_filter.accessibleDescription()
            assert all(progress in projects.item(i).toolTip() for i in range(projects.count()))
            assert panel.batch_filter.currentData() == batch_key
            assert panel.search.text() == '匿名检修' and panel.status_buttons['待开发'].isChecked()
            assert projects.currentItem() is selected and selected.isSelected()
            assert projects.itemAt(QPoint(5, 5)) is anchor and projects.visualItemRect(anchor).top() == offset
            assert [projects.item(i).data(Qt.UserRole) for i in range(projects.count())] == ids
            assert panel.project_splitter.sizes() == splitter_sizes
            assert panel.size().width() == 660 and panel.size().height() == 480
            if delivered:
                for mode in ('light', 'dark'):
                    set_theme(mode, persist=False)
                    qt_app.processEvents()
                    assert panel.grab().save(str(tmp_path / f'batch-progress-660-{mode}.png'))
        panel.project_filter_button.click()
        qt_app.processEvents()
        panel.batch_filter.setFocus()
        QTest.keyClick(panel.batch_filter, Qt.Key_Up)
        assert not panel.batch_progress_summary.isVisible()
    finally:
        panel.document_pane.shutdown()
        panel.hide()
        set_theme('light', persist=False)
