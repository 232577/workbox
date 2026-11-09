"""批次号码优先完整呈现；系统主题使用实际绘制主题。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

import fluent_views
from compact_ui import ProjectList, populate
from theme import manager, set_theme


def test_batch_metadata_shares_a_line_when_it_fits_and_wraps_when_needed():
    qapp = QApplication.instance() or QApplication([])
    view = ProjectList()
    view.resize(350, 260)
    view.show()
    try:
        rows = [
            {'id': 1, 'name': '匿名项目一'},
            {'id': 2, 'name': '匿名项目二', 'batch_markers': [
                {'label': 'B261109-1', 'code': 'B20261109-1', 'color': '#507080',
                 'total_count': 4, 'delivered_count': 2, 'remaining_count': 2},
            ]},
            {'id': 3, 'name': '设备设施定期巡查和维护计划补充文件项目记录' * 2},
        ]
        populate(view, rows, lambda row: row['name'] + '\n待交付 · 生成材料')
        qapp.processEvents()
        plain, batch, long_name = (view.visualItemRect(view.item(index)).height() for index in range(3))
        assert batch <= plain + 4
        assert long_name > batch
        assert 'B20261109-1 (B261109-1) 已交付2/4，剩余2' in view.item(1).toolTip()

        view.resize(200, 260)
        qapp.processEvents()
        narrow_plain = view.visualItemRect(view.item(0)).height()
        narrow_batch = view.visualItemRect(view.item(1)).height()
        assert narrow_batch > narrow_plain + 10
    finally:
        view.close()
        view.deleteLater()


def test_narrow_batch_layout_preserves_whole_code_and_visible_more_count():
    app = QApplication.instance() or QApplication([])
    font = QFont(app.font()); font.setPixelSize(11)
    metrics = QFontMetrics(font)
    markers = [{'label': 'R261105-12345', 'color': '#507080'},
               {'label': 'G261105-012', 'color': '#78658A'}]
    width = 24 + metrics.horizontalAdvance(markers[0]['label']) + 5 + metrics.horizontalAdvance('+1批')
    badges, summary = fluent_views._batch_badge_layout(markers, metrics, width)
    assert [value[0]['label'] for value in badges] == [markers[0]['label']]
    assert summary == '+1批'
    assert sum(value[1] + 5 for value in badges) + metrics.horizontalAdvance(summary) <= width
    badges, summary = fluent_views._batch_badge_layout(markers, metrics, 500)
    assert [value[0]['label'] for value in badges] == [m['label'] for m in markers]
    assert summary == ''


def test_system_dark_marker_uses_resolved_dark_colors(monkeypatch):
    app = QApplication.instance() or QApplication([])
    theme = manager()
    previous_scheme = theme.system_scheme
    calls = []
    original = fluent_views._batch_badge_background
    def observe(color, mode):
        calls.append(mode)
        return original(color, mode)
    monkeypatch.setattr(fluent_views, '_batch_badge_background', observe)
    view = ProjectList()
    try:
        theme.system_scheme = Qt.ColorScheme.Dark
        set_theme('system', persist=False)
        populate(view, [{'id': 1, 'name': '匿名系统主题项目', 'batch_markers': [
            {'label': 'R261105-1', 'code': 'R20261105-1', 'color': '#507080'}]}], lambda r: r['name'])
        view.resize(280, 180); view.show(); app.processEvents(); view.grab()
        assert calls and set(calls) == {'dark'}
    finally:
        view.hide(); view.deleteLater()
        theme.system_scheme = previous_scheme
        set_theme('light', persist=False)
