"""常用窗口把空间交给项目与实际文件，项目操作跟随当前选择。"""
from pathlib import Path
from collections import Counter
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import install_theme, set_theme, tokens


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('\n'.join(f'{i}. 工作台项目{i}' for i in range(1, 13)))
    root = Path(service.workspaces.get_workspace(1)['path'])
    for i in range(12):
        (root / f'材料{i:02d}.txt').write_text('匿名材料', encoding='utf-8')
    value = CompactPanel(service)
    value.resize(1000, 700)
    value.show()
    item = next(value.project_list.item(i) for i in range(value.project_list.count())
                if value.project_list.item(i).data(Qt.UserRole) == 1)
    value.project_list.setCurrentItem(item)
    for _ in range(200):
        app.processEvents()
        time.sleep(.01)
        if value.document_pane.worker is None and value.document_pane.table.rowCount() == 12:
            break
    QTest.qWait(100)
    yield value
    value.document_pane.shutdown()
    value.hide()
    install_theme()


def test_common_window_gives_files_six_complete_rows_and_keeps_filters_reachable(panel):
    files = panel.document_pane.table
    assert files.rowCount() == 12
    assert files.viewport().height() >= 6 * files.rowHeight(0)
    assert panel.project_list.viewport().height() >= 350
    for control in (panel.batch_filter, panel.bulk_mode, panel.project_filter_button):
        assert control.isVisible()
    panel.project_filter_button.click()
    for control in (panel.archive_filter, panel.due_filter, panel.type_filter, panel.urgent_filter):
        assert control.isVisible()
    panel.project_filter_button.click()
    before = panel.current_project['id']
    panel.resize(660, 480)
    QTest.qWait(120)
    assert panel.size().width() == 660 and panel.size().height() == 480
    assert panel.document_pane.table.viewport().height() >= 40
    panel.resize(1000, 700)
    QTest.qWait(120)
    assert panel.current_project['id'] == before


def test_selected_project_has_discoverable_actions_without_right_click(panel, monkeypatch):
    control = getattr(panel, 'project_actions_button', None)
    assert control is not None, '所选项目需要可见的操作入口'
    assert control.isVisible() and control.isEnabled()
    calls = []
    monkeypatch.setattr(panel, 'show_project_context_menu', lambda pid, pos: calls.append(pid))
    control.click()
    assert calls == [1]
    panel.project_list.clearSelection()
    assert not control.isEnabled()


def test_rearranged_search_keeps_scope_when_switching_pages(panel):
    panel.search.setText('工作台项目1')
    expected = [panel.project_list.item(i).data(Qt.UserRole)
                for i in range(panel.project_list.count())]
    assert 1 in expected and len(expected) < 12
    panel.advanced_actions['zip'].trigger()
    assert panel.search.isVisible()
    panel.nav_projects.click()
    assert panel.search.text() == '工作台项目1'
    assert [panel.project_list.item(i).data(Qt.UserRole)
            for i in range(panel.project_list.count())] == expected


def test_wide_default_leaves_room_for_files_and_manual_split_still_wins(panel):
    panel.resize(1920, 1000)
    QTest.qWait(150)
    split = panel.project_splitter
    assert 380 <= split.sizes()[0] <= 440
    handle = split.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start + QPoint(160, 0), 50)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=handle.rect().center())
    QTest.qWait(100)
    assert split.sizes()[0] > 500
    panel.resize(1800, 950)
    QTest.qWait(100)
    assert split.sizes()[0] > 440, '手动宽度不能被默认分配覆盖'


def test_bulk_type_action_stays_visible_and_targets_all_selected_projects(panel, monkeypatch):
    calls = []
    def choose(service, parent, projects):
        calls.append({project['id'] for project in projects})
        return False, None
    monkeypatch.setattr('project_types_ui.choose_project_type', choose)
    panel.bulk_mode.setChecked(True)
    items = [panel.project_list.item(i) for i in range(2)]
    for item in items:
        item.setSelected(True)
    expected = {item.data(Qt.UserRole) for item in items}
    assert panel.change_type_button.isVisible()
    assert panel.change_type_button.isEnabled()
    panel.change_type_button.click()
    assert calls == [expected]
    panel.bulk_mode.setChecked(False)
    assert not panel.change_type_button.isVisible()
    panel.project_list.setCurrentItem(items[0])
    assert panel.change_type_button.isVisible()
    assert panel.document_side.isAncestorOf(panel.change_type_button)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_selected_navigation_icon_is_legible_on_its_background(panel, mode):
    install_theme(panel.service)
    set_theme(mode, persist=False)
    panel.nav_projects.click()
    image = panel.nav_projects.icon().pixmap(16, 16).toImage()
    opaque = Counter(image.pixelColor(x, y).rgb()
                     for x in range(image.width()) for y in range(image.height())
                     if image.pixelColor(x, y).alpha() >= 200)
    assert opaque
    foreground = QColor(opaque.most_common(1)[0][0])
    background = QColor(tokens(mode)['nav_selected'])

    def luminance(color):
        values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in (color.redF(), color.greenF(), color.blueF())]
        return sum(value * weight for value, weight in zip(values, (.2126, .7152, .0722)))

    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    assert (light + .05) / (dark + .05) >= 3, (mode, foreground.name(), background.name())
    install_theme()
