"""常用窗口把空间交给项目与实际文件，项目操作跟随当前选择。"""
from pathlib import Path
from collections import Counter
import time

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from workbox.desktop.compact_ui import CompactPanel
from workbox.desktop.desktop_service import DesktopService
from workbox.desktop.theme import install_theme, set_theme, tokens


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
    for control in (panel.added_date_filter, panel.select_all_button, panel.project_filter_button):
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
    assert 340 <= split.sizes()[0] <= 380
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


@pytest.mark.parametrize('width, height, manual_split', [(913, 610, False), (1200, 500, True)])
def test_bulk_selection_keeps_window_and_project_split_widths(panel, width, height, manual_split):
    panel.resize(width, height)
    QTest.qWait(100)
    split = panel.project_splitter
    if manual_split:
        split.moveSplitter(0, 1)
    before_size = panel.size()
    before_split = split.sizes()
    assert panel.document_side.isVisible() and all(before_split)
    before_document_rect = panel.document_side.geometry()

    panel.select_all_button.click()
    panel.bulk_tools_button.click()
    QTest.qWait(100)
    assert panel.size() == before_size
    assert panel.document_side.isVisible()
    assert split.sizes() == before_split
    assert panel.document_side.geometry() == before_document_rect
    assert panel.bulk_bar.parentWidget() is panel and panel.bulk_bar.isWindow()
    for control in (panel.bulk_action, panel.bulk_apply, panel.change_type_button,
                    panel.bulk_select_all_button, panel.clear_selection_button):
        assert control.isVisible()
        assert control.width() >= control.minimumSizeHint().width()
        assert panel.bulk_bar.rect().contains(control.mapTo(panel.bulk_bar, control.rect().bottomRight()))

    panel.project_list.item(0).setSelected(True)
    panel.project_list.item(1).setSelected(True)
    QTest.qWait(100)
    assert panel.size() == before_size
    assert split.sizes() == before_split
    assert panel.bulk_apply.isEnabled()

    panel.clear_selection_button.click()
    QTest.qWait(100)
    assert panel.size() == before_size
    assert split.sizes() == before_split


def test_bulk_selection_keeps_narrow_queue_and_detail_navigation(panel):
    panel.resize(660, 480)
    panel.show_project_queue()
    QTest.qWait(100)
    before_size = panel.size()
    before_split = panel.project_splitter.sizes()
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()

    panel.project_list.setCurrentRow(0)
    panel.project_list.item(1).setSelected(True)
    panel.bulk_tools_button.click()
    QTest.qWait(100)
    assert panel.size() == before_size
    assert panel.project_splitter.sizes() == before_split
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()
    assert panel.bulk_apply.isVisible() and not panel.project_back_button.isVisible()
    assert panel.bulk_action.width() < panel.project_side.width() / 2
    assert QRect(panel.mapToGlobal(QPoint()), panel.size()).contains(panel.bulk_bar.geometry())
    panel.bulk_select_all_button.click()
    assert len(panel.project_list.selectedItems()) == panel.project_list.count()

    panel.clear_selection_button.click()
    panel.project_list.setCurrentRow(0)
    assert panel.project_detail_button.isVisible()
    panel.project_detail_button.click()
    assert panel.document_side.isVisible() and panel.project_back_button.isVisible()
    panel.project_back_button.click()
    assert panel.project_side.isVisible() and not panel.document_side.isVisible()
    assert panel.size() == before_size


@pytest.mark.parametrize('width, height', [(913, 610), (660, 480)])
def test_ctrl_selection_keeps_scrolled_project_rows_in_place(panel, width, height):
    panel.show_project_queue()
    panel.resize(width, height)
    QTest.qWait(100)
    projects = panel.project_list
    bar = projects.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    viewport = projects.viewport()
    items = [projects.item(i) for i in range(projects.count())
             if viewport.rect().contains(projects.visualItemRect(projects.item(i)))]
    first, second = items[:2]
    QTest.mouseClick(viewport, Qt.LeftButton, pos=projects.visualItemRect(first).center())
    QTest.qWait(30)
    before = projects.geometry(), viewport.geometry(), bar.value(), projects.visualItemRect(first)

    QTest.mouseClick(viewport, Qt.LeftButton, Qt.ControlModifier,
                     pos=projects.visualItemRect(second).center())
    QTest.qWait(30)
    assert len(projects.selectedItems()) == 2 and panel.bulk_tools_button.isVisible()
    assert not panel.bulk_bar.isVisible()
    assert (projects.geometry(), viewport.geometry(), bar.value(), projects.visualItemRect(first)) == before

    panel.bulk_tools_button.setFocus()
    QTest.keyClick(panel.bulk_tools_button, Qt.Key_Space)
    assert panel.bulk_bar.isVisible()
    QTest.keyClick(panel.bulk_bar, Qt.Key_Escape)
    assert not panel.bulk_bar.isVisible() and not panel.bulk_tools_button.isChecked()
    assert len(projects.selectedItems()) == 2
    assert (projects.geometry(), viewport.geometry(), bar.value(), projects.visualItemRect(first)) == before

    QTest.mouseClick(viewport, Qt.LeftButton, Qt.ControlModifier,
                     pos=projects.visualItemRect(second).center())
    QTest.qWait(30)
    assert projects.selectedItems() == [first] and panel.select_all_button.isVisible()
    assert (projects.geometry(), viewport.geometry(), bar.value(), projects.visualItemRect(first)) == before


def test_bulk_popup_closes_with_the_workbench(panel):
    panel.select_all_button.click()
    panel.bulk_tools_button.click()
    assert panel.bulk_bar.isVisible()

    panel.close()
    assert not panel.bulk_bar.isVisible() and not panel.bulk_tools_button.isChecked()

    panel.show()
    panel.bulk_tools_button.click()
    assert panel.bulk_bar.isVisible()


def test_bulk_action_popup_fits_long_type_names_without_widening_toolbar(panel):
    name = '匿名自定义类型' * 4
    kind = panel.service.create_project_type(name)
    panel.refresh_type_controls()
    panel.select_all_button.click()
    panel.bulk_tools_button.click()
    combo = panel.bulk_action
    width = combo.width()
    combo.showPopup()
    try:
        view = combo.view()
        assert view.viewport().width() >= view.sizeHintForColumn(0)
    finally:
        combo.hidePopup()
    combo.setCurrentIndex(combo.findData(f"type:{kind['id']}"))
    assert combo.width() == width
    assert combo.toolTip() == f'项目类型：{name}'


def test_bulk_type_action_stays_visible_and_targets_all_selected_projects(panel, monkeypatch):
    calls = []
    def choose(service, parent, projects):
        calls.append({project['id'] for project in projects})
        return False, None
    monkeypatch.setattr('workbox.desktop.project_types_ui.choose_project_type', choose)
    panel.project_list.clearSelection()
    items = [panel.project_list.item(i) for i in range(2)]
    for item in items:
        item.setSelected(True)
    expected = {item.data(Qt.UserRole) for item in items}
    panel.bulk_tools_button.click()
    assert panel.change_type_button.isVisible()
    assert panel.change_type_button.isEnabled()
    panel.change_type_button.click()
    assert calls == [expected]
    panel.bulk_tools_button.click()
    panel.clear_selection_button.click()
    assert not panel.change_type_button.isVisible()
    panel.project_list.setCurrentItem(items[0])
    assert not panel.change_type_button.isVisible()
    assert panel.project_metadata.isVisible()
    menu = panel.project_context_menu(items[0].data(Qt.UserRole))
    assert any(action.text() == '项目类型' and action.isEnabled() for action in menu.actions())


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
