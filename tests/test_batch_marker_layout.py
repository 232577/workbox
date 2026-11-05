"""批次号码优先完整呈现；系统主题使用实际绘制主题。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication

import fluent_views
from compact_ui import ProjectList, populate
from theme import manager, set_theme


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
