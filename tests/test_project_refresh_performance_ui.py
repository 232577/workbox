"""Anonymous UI work-count regressions; no timing thresholds or real user data."""
from collections import Counter

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel, ProjectList, populate
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def panel(qapp, tmp_path):
    service = DesktopService(tmp_path / 'anonymous' / 'workbox.db')
    service.receive_text('1. 匿名搜索甲系统\n2. 匿名搜索乙工具\n3. 匿名搜索丙平台')
    widget = CompactPanel(service)
    widget.resize(1160, 780)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.document_pane.shutdown()
    widget.hide()
    widget.deleteLater()
    qapp.processEvents()


def test_project_height_resize_does_not_force_all_row_layout(qapp, monkeypatch):
    view = ProjectList()
    view.resize(440, 300)
    populate(view, [dict(id=i, name=f'匿名长项目名称{i}资料系统') for i in range(100)], lambda row: row['name'])
    view.show()
    qapp.processEvents()
    view.setCurrentRow(40)
    view.verticalScrollBar().setValue(500)
    selected = view.currentItem()
    bar = view.verticalScrollBar()
    anchor = view.itemAt(QPoint(5, 5))
    anchor_position = view.visualItemRect(anchor).top()
    maximum, height = bar.maximum(), view.viewport().height()
    calls = []
    measurements = []
    original = view.doItemsLayout
    measure = view.itemDelegate().sizeHint
    monkeypatch.setattr(view, 'doItemsLayout', lambda: (calls.append(True), original())[1])
    monkeypatch.setattr(view.itemDelegate(), 'sizeHint', lambda *args: (measurements.append(True), measure(*args))[1])
    try:
        view.resize(view.width(), view.height() + 40)
        qapp.processEvents()
        assert not calls, 'Height-only changes must not force full row measurement'
        assert len(measurements) < view.count(), 'Height-only changes must not measure the entire model'
        assert view.currentItem() is selected
        assert bar.maximum() == maximum - (view.viewport().height() - height)
        assert view.itemAt(QPoint(5, 5)) is anchor
        assert view.visualItemRect(anchor).top() == anchor_position
        maximum, height = bar.maximum(), view.viewport().height()
        view.resize(view.width(), view.height() - 120)
        qapp.processEvents()
        assert bar.maximum() == maximum + height - view.viewport().height()
        assert view.currentItem() is selected
        assert view.itemAt(QPoint(5, 5)) is anchor
        assert view.visualItemRect(anchor).top() == anchor_position
        bar.setValue(bar.maximum())
        last = view.visualItemRect(view.item(view.count() - 1))
        assert last.top() >= 0 and last.bottom() <= view.viewport().height()
        view.resize(view.width() - 50, view.height())
        qapp.processEvents()
        assert calls, 'Width changes must still remeasure wrapped project rows'
        assert view.currentItem() is selected
    finally:
        view.close()
        view.deleteLater()


def test_project_search_and_filters_skip_unrelated_full_refresh(panel, monkeypatch):
    counts = Counter()
    for obj, name in [(panel.service, 'list_project_types'), (panel.service, 'pending_receipts'),
                      (panel.service, 'list_zips'), (panel.service.snapshots, 'list_versions'),
                      (panel.service.snapshots, 'list_sources'), (panel.service.db, 'today_delivery_summary')]:
        original = getattr(obj, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(obj, name, counted)
    panel.search.setText('甲')
    assert panel.project_list.count() == 1
    panel.status_buttons['待开发'].click()
    panel.urgent_filter.setChecked(True)
    assert panel.project_list.count() == 0
    panel.clear_project_filter('urgent')
    assert panel.project_list.count() == 1
    panel.archive_filter.setCurrentIndex(1)
    panel.reset_project_filters()
    assert panel.project_list.count() == 1
    assert not counts, dict(counts)
    panel.refresh()
    assert all(counts[name] for name in ('list_project_types', 'pending_receipts', 'list_zips',
                                       'list_versions', 'list_sources', 'today_delivery_summary'))


def test_hidden_zip_query_refreshes_when_entered_and_search_is_immediate(panel, monkeypatch):
    queries = []
    original = panel.service.list_zips
    def list_zips(query, unmatched):
        queries.append(query)
        return original(query, unmatched)
    monkeypatch.setattr(panel.service, 'list_zips', list_zips)
    panel.search.setText('甲')
    panel.search.setText('乙')
    assert not queries
    panel.tabs.setCurrentIndex(1)
    assert queries == ['乙']
    panel.search.setText('丙')
    assert queries == ['乙', '丙']
    panel.tabs.setCurrentIndex(0)
    panel.search.setText('甲')
    panel.tabs.setCurrentIndex(1)
    assert queries == ['乙', '丙', '甲']


def test_single_selection_reuses_the_detail_already_read(panel, monkeypatch):
    calls = []
    original = panel.service.get_project
    def get_project(project_id):
        calls.append(project_id)
        return original(project_id)
    monkeypatch.setattr(panel.service, 'get_project', get_project)
    panel.project_list.setCurrentRow(0)
    project_id = panel.project_list.currentItem().data(Qt.UserRole)
    assert calls == [project_id]
    assert panel.current_project['id'] == project_id
    assert panel.change_type_button.isEnabled()


def test_failed_full_zip_refresh_retries_same_query_on_entry_and_keeps_warning(panel, monkeypatch):
    panel.search.setText('甲')
    panel.refresh()
    original = panel.service.snapshots.list_versions
    def fail(*args, **kwargs):
        raise OSError('匿名归档读取失败')
    monkeypatch.setattr(panel.service.snapshots, 'list_versions', fail)
    panel.refresh()
    warning = panel.notice.text()
    assert '匿名归档读取失败' in warning
    monkeypatch.setattr(panel.service.snapshots, 'list_versions', original)
    queries = []
    list_zips = panel.service.list_zips
    monkeypatch.setattr(panel.service, 'list_zips', lambda query, unmatched: (
        queries.append(query), list_zips(query, unmatched))[1])
    panel.tabs.setCurrentIndex(1)
    assert queries == ['甲'], 'A failed full refresh must leave ZIP contents dirty even at the same query'
    assert panel.notice.text() == warning


def test_local_filter_keeps_persistent_warning_and_full_refresh_after_drag(panel, monkeypatch):
    panel.show_notice('匿名持久警告')
    panel.search.setText('甲')
    assert panel.notice.text() == '匿名持久警告'
    counts = []
    original = panel.service.pending_receipts
    monkeypatch.setattr(panel.service, 'pending_receipts', lambda: (counts.append(True), original())[1])
    bar = panel.project_list.verticalScrollBar()
    bar.setSliderDown(True)
    panel.refresh()
    panel.search.setText('乙')
    assert not counts
    bar.setSliderDown(False)
    panel._finish_scroll()
    assert counts, 'A deferred full business refresh must not be downgraded to a local filter refresh'
    assert panel.project_list.count() == 1
