"""按首次记录日期显示项目数；数字和清单共用同一筛选范围。"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from compact_ui import CompactPanel
from desktop_service import DesktopService
from fluent_views import PROJECT_BATCH_MARKERS_ROLE, _batch_badge_parts


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    receipt = service.receive_text('\n'.join(f'{i}. 匿名记录项目{i}' for i in range(1, 7)))
    with service.db.connect() as connection:
        for pid in receipt['project_ids']:
            connection.execute('UPDATE projects SET created_at=?,status=? WHERE id=?',
                (f'2026-11-{4 + pid:02}T16:30:00Z',
                 ('待开发', '开发中', '待交付', '已交付', '待开发', '待交付')[pid - 1], pid))
    widget = CompactPanel(service)
    widget.resize(660, 540)
    widget.show()
    app.processEvents()
    yield widget
    widget.document_pane.shutdown()
    widget.hide()


def ids(panel):
    return {panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())}


def test_range_and_status_counts_share_the_list_scope(panel):
    panel.added_date_filter.set_range('2026-11-06', '2026-11-09')
    assert ids(panel) == {1, 2, 3, 4}
    assert panel.project_status_counts == {'待开发': 1, '开发中': 1, '待交付': 1, '已交付': 1}
    assert panel.status_buttons[None].text().replace('\n', ' ') == '全部 4'
    panel.status_buttons['待交付'].click()
    assert ids(panel) == {3}
    assert panel.project_list_heading.text() == '项目清单 · 1件'
    assert panel.status_buttons[None].text().replace('\n', ' ') == '全部 4'
    assert panel.status_buttons['开发中'].text().replace('\n', ' ') == '开发中 1'
    assert '首次记录' in panel.project_count_context.text()
    assert '日常项目' in panel.project_count_context.text()
    assert '当前状态' in panel.status_segments.accessibleDescription()


def test_secondary_filters_collapsed_but_active_batch_is_clearable(panel):
    assert panel.added_date_filter.isVisible()
    assert not panel.type_filter.isVisible() and not panel.batch_filter.isVisible()
    panel.project_filter_button.click()
    assert panel.batch_filter.isVisible() and panel.type_filter.isVisible()
    panel.batch_filter.setCurrentIndex(1)
    panel.project_filter_button.click()
    assert not panel.batch_progress_summary.isVisible()
    chip = panel.project_filter_chips['batch']
    assert chip.isVisible()
    assert '1' in panel.project_filter_button.text()
    chip.click()
    assert panel.batch_filter.currentData() is None and not chip.isVisible()


def test_rows_show_batch_identity_without_remaining_count(panel):
    index = panel.project_list.model().index(0, 0)
    marker = index.data(PROJECT_BATCH_MARKERS_ROLE)[0]
    assert '待完成' in _batch_badge_parts(marker)[1]
    layout = panel.project_list.itemDelegate()._row_layout(index, 550)
    badges = layout[3][3]
    assert badges and _batch_badge_parts(badges[0][0])[1] == ''
    assert index.data(PROJECT_BATCH_MARKERS_ROLE)[0] == marker
    assert '待完成' in index.data(Qt.ToolTipRole)


@pytest.mark.parametrize('leave', ['hide', 'page', 'escape'])
def test_filter_popup_closes_with_its_owner(panel, leave):
    panel.project_filter_button.click()
    QApplication.processEvents()
    assert panel.project_filter_panel.isVisible()
    if leave == 'hide':
        panel.hide()
    elif leave == 'page':
        panel.tabs.setCurrentIndex(1)
    else:
        QTest.keyClick(panel.project_filter_panel, Qt.Key_Escape)
    QApplication.processEvents()
    assert not panel.project_filter_panel.isVisible()
    assert not panel.project_filter_button.isChecked()


def test_range_export_and_empty_recovery(panel):
    requests = []
    panel.project_table_export_requested.connect(lambda *args: requests.append(args))
    panel.added_date_filter.set_range('2026-11-07', '2026-11-08')
    panel.request_project_table_export('filtered')
    assert set(requests[0][0]) == {2, 3}
    assert '2026-11-07 至 2026-11-08' in requests[0][1]
    panel.added_date_filter.set_range('2025-01-01', '2025-01-02')
    assert not ids(panel)
    assert all(value == 0 for value in panel.project_status_counts.values())
    assert panel.empty_clear_filters_button.isVisible()
    panel.empty_clear_filters_button.click()
    assert panel.added_date_filter.selection() is None
    assert len(ids(panel)) == 6


def test_range_refresh_preserves_selection_and_focus_receipt_resets_range(panel):
    panel.added_date_filter.set_range('2026-11-06', '2026-11-09')
    row = panel.project_list.item(1)
    panel.project_list.setCurrentItem(row)
    panel.refresh()
    assert panel.project_list.currentItem() is row
    assert panel.added_date_filter.selection() == ('2026-11-06', '2026-11-09')
    assert panel.focus_projects([6])
    assert panel.added_date_filter.selection() is None
    assert panel.project_list.currentItem().data(Qt.UserRole) == 6


@pytest.mark.parametrize('width', [660, 900, 1200])
def test_counts_and_date_controls_fit_without_expanding_the_window(panel, width):
    panel.resize(width, 540)
    QApplication.processEvents()
    assert panel.width() == width
    for item in (*panel.status_buttons.values(), panel.added_date_filter):
        rect = item.rect().translated(item.mapTo(panel, item.rect().topLeft()))
        assert panel.rect().contains(rect)
        assert item.width() >= item.minimumSizeHint().width()
    assert panel.project_list.height() >= 120
