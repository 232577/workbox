"""项目批次标记保持项目身份与原顺序。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor

from compact_ui import CompactPanel, ProjectList, populate
from desktop_service import DesktopService
from parser import ParsedProject
import fluent_views
from theme import set_theme


def test_disappearing_creation_batch_resets_filter_and_reloads_current_scope(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'workbox.db')
    service.capture_message('匿名旧项目', '旧批次', [ParsedProject('匿名旧项目系统')])
    service.capture_message('匿名另一项目', '另一批次', [ParsedProject('匿名另一项目系统')])
    panel = CompactPanel(service)
    try:
        panel.search.setText('匿名')
        panel.status_buttons['待开发'].setChecked(True)
        panel.batch_filter.setCurrentIndex(panel.batch_filter.findData('batch:1'))
        assert panel.project_list.count() == 1
        panel.project_list.setCurrentRow(0)
        service.receive_text('1. 匿名旧项目系统\n2. 匿名新项目工具')
        snapshot = service.project_workbench_snapshot
        calls = []
        def observed(*args, **kwargs):
            calls.append(args[2])
            return snapshot(*args, **kwargs)
        monkeypatch.setattr(service, 'project_workbench_snapshot', observed)
        panel.refresh()
        expected = snapshot('匿名', '待开发')['projects']
        assert calls == ['batch:1', None]
        assert panel.batch_filter.currentData() is None
        assert panel.search.text() == '匿名' and panel.status_buttons['待开发'].isChecked()
        assert [panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())] == [row['id'] for row in expected]
        assert panel.project_scope[2] is None
        assert not panel.project_list.selectedItems()
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_markers_update_in_place_and_expose_all_labels(tmp_path):
    app = QApplication.instance() or QApplication([])
    view = ProjectList()
    markers = [{'key': f'receipt:{i}', 'label': f'R{i}', 'code': f'完整编号{i}',
                'color': '#337799', 'source_kind': 'receipt', 'source_id': i,
                'received_at': None} for i in range(3)]
    rows = [{'id': 2, 'name': '乙', 'batch_markers': markers, 'batch_name': '原目录'},
            {'id': 1, 'name': '甲', 'batch_markers': [], 'batch_name': '原目录'}]
    populate(view, rows, lambda row: row['name'] + '\n待开发')
    item = view.item(0)
    view.setCurrentItem(item)
    assert [view.item(i).data(Qt.UserRole) for i in range(view.count())] == [2, 1]
    assert item.data(fluent_views.PROJECT_BATCH_MARKERS_ROLE) == markers
    assert all(m['code'] in item.toolTip() for m in markers)
    assert all(m['code'] in item.data(Qt.AccessibleDescriptionRole) for m in markers)
    rows[0]['batch_markers'] = markers[:1]
    populate(view, rows, lambda row: row['name'] + '\n待开发')
    assert view.item(0) is item and item.isSelected()
    assert item.data(fluent_views.PROJECT_BATCH_MARKERS_ROLE) == markers[:1]
    view.close()


def test_workbench_uses_one_snapshot_and_has_no_task_page(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'workbox.db')
    service.db.create_capture('消息', '原批次', [('匿名项目', False)])
    row = service.list_projects()[0]
    marker = {'key': 'receipt:7', 'label': 'R7', 'code': 'R20261105-7',
              'color': '#337799', 'source_kind': 'receipt', 'source_id': 7, 'received_at': None}
    calls = []
    def snapshot(*args, **kwargs):
        calls.append((args, kwargs))
        return {'projects': [dict(row, batch_markers=[marker])], 'batch_options': [marker], 'warnings': [],
                'status_counts': {'待开发': 1, '开发中': 0, '待交付': 0, '已交付': 0}, 'total_count': 1}
    monkeypatch.setattr(service, 'project_workbench_snapshot', snapshot, raising=False)
    panel = CompactPanel(service)
    try:
        assert len(calls) == 1
        assert panel.project_list.item(0).data(fluent_views.PROJECT_BATCH_MARKERS_ROLE) == [marker]
        assert panel.batch_filter.findData(marker['key']) >= 0
        assert panel.tabs.currentIndex() == 0 and panel.task_groups_page is None
        assert not hasattr(panel, 'nav_task_groups')
        assert 'history' not in panel.advanced_actions
    finally:
        panel.document_pane.shutdown()
        panel.hide()


def test_marker_colors_remain_visible_in_both_themes_and_selected(tmp_path):
    app = QApplication.instance() or QApplication([])
    view = ProjectList()
    markers = [{'key': str(i), 'label': f'R{i}', 'code': f'R20261105-{i}',
                'color': color} for i, color in enumerate(('#D34836', '#2877C5', '#3F9148'))]
    rows = [{'id': 1, 'name': '长名称匿名项目工作台', 'batch_markers': markers, 'batch_name': '原目录'}]
    populate(view, rows, lambda row: row['name'] + '\n待开发 · 未分类')
    view.resize(240, 180)
    view.show()
    view.setCurrentRow(0)
    try:
        for mode in ('light', 'dark'):
            set_theme(mode, persist=False)
            app.processEvents()
            image = view.grab().toImage()
            assert image.save(str(tmp_path / f'markers-{mode}.png'))
            pixels = {image.pixelColor(x, y).name().lower()
                      for x in range(image.width()) for y in range(image.height())}
            assert '#d34836' in pixels and '#2877c5' in pixels
            assert '#3f9148' not in pixels
            background = fluent_views._batch_badge_background('#D34836', mode).name().lower()
            assert sum(image.pixelColor(x, y).name().lower() == background
                       for x in range(image.width()) for y in range(image.height())) >= 100
            assert view.currentItem().isSelected()
    finally:
        set_theme('light', persist=False)
        view.close()
