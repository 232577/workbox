"""Added-date filtering remains distinct from batch progress and due dates."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    receipt = service.receive_text('\n'.join(f'{i}. 匿名项目{i}' for i in range(1, 6)))
    with service.db.connect() as connection:
        for pid in receipt['project_ids']:
            connection.execute('UPDATE projects SET created_at=? WHERE id=?',
                ('2026-11-05T16:30:00Z' if pid <= 3 else '2026-11-06T16:30:00Z', pid))
    service.set_status(1, '待交付')
    widget = CompactPanel(service)
    widget.resize(900, 640)
    widget.show()
    yield widget
    widget.document_pane.shutdown()
    widget.hide()


def ids(panel):
    return {panel.project_list.item(i).data(Qt.UserRole) for i in range(panel.project_list.count())}


def test_added_day_batch_and_status_intersect_without_changing_full_batch_total(panel):
    picker = panel.added_date_filter
    assert picker.accessibleName() == '新增日期筛选'
    assert picker.itemData(0) is None
    assert {picker.itemData(i) for i in range(1, picker.count())} == {'2026-11-06', '2026-11-07'}
    picker.setCurrentIndex(picker.findData('2026-11-06'))
    assert ids(panel) == {1, 2, 3}
    panel.batch_filter.setCurrentIndex(1)
    assert panel.batch_progress_summary.text() == '待完成4，共5'
    panel.status_buttons['待交付'].click()
    assert ids(panel) == {1}
    assert panel.batch_progress_summary.text() == '待完成4，共5'
    assert '待交付1' in panel.batch_filter.accessibleDescription()
    picker.setCurrentIndex(picker.findData('2026-11-07'))
    assert ids(panel) == set() and panel.empty.isVisible()
    assert picker.currentData() == '2026-11-07'
    panel.status_buttons[None].click()
    assert ids(panel) == {4, 5}
    picker.setCurrentIndex(0)
    assert ids(panel) == {1, 2, 3, 4, 5}


def test_recorded_added_day_visible_and_unchanged_after_status_refresh(panel):
    picker = panel.added_date_filter
    picker.setCurrentIndex(picker.findData('2026-11-06'))
    row = next(panel.project_list.item(i) for i in range(panel.project_list.count())
               if panel.project_list.item(i).data(Qt.UserRole) == 1)
    panel.project_list.setCurrentItem(row)
    assert '新增日期：2026-11-06' in row.toolTip()
    assert '新增日期：2026-11-06' in row.data(Qt.AccessibleDescriptionRole)
    assert '新增：2026-11-06' in panel.project_metadata.text()
    panel.service.set_status(1, '开发中')
    panel.refresh()
    assert panel.project_list.currentItem() is row
    assert picker.currentData() == '2026-11-06'
    assert '新增：2026-11-06' in panel.project_metadata.text()


def test_date_filter_refreshes_only_project_view_and_preserves_explicit_empty_date(panel, monkeypatch):
    monkeypatch.setattr(panel, '_refresh_other_views', lambda: pytest.fail('date change refreshed unrelated views'))
    picker = panel.added_date_filter
    picker.setCurrentIndex(picker.findData('2026-11-07'))
    panel.archive_filter.setCurrentIndex(panel.archive_filter.findData(True))
    assert picker.currentData() == '2026-11-07'
    assert ids(panel) == set()
    assert '无项目' in picker.currentText()
    picker.setCurrentIndex(0)
    panel.archive_filter.setCurrentIndex(0)
    assert ids(panel) == {1, 2, 3, 4, 5}


def test_receipt_view_clears_added_day_and_focuses_the_requested_project(panel):
    picker = panel.added_date_filter
    picker.setCurrentIndex(picker.findData('2026-11-06'))
    assert 4 not in ids(panel)
    assert panel.focus_projects([4]) is True
    assert picker.currentData() is None
    assert panel.project_list.currentItem().data(Qt.UserRole) == 4
