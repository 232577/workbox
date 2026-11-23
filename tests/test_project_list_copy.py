"""项目清单使用名称角色作为复制内容。"""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication

from workbox.desktop.compact_ui import ProjectList, populate


@pytest.fixture
def project_list(monkeypatch):
    app = QApplication.instance() or QApplication([])
    copied = []
    clipboard = SimpleNamespace(setText=copied.append)
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clipboard)
    view = ProjectList()
    rows = [
        {'id': 2, 'name': '样例（C#）管理平台'},
        {'id': 3, 'name': '样例测试项目'},
        {'id': 1, 'name': '文档 2.0 分发工具'},
        {'id': 4, 'name': '未选择的样例项目'},
    ]
    populate(view, rows, lambda row: row['name'] + '\n待交付 · 生成材料 · R20261123-1')
    yield view, copied
    view.close()


def test_copy_selected_project_uses_plain_name(project_list):
    view, copied = project_list
    view.setCurrentRow(0)

    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)

    assert copied == ['样例（C#）管理平台']
    assert view.currentItem().text() == '样例（C#）管理平台\n待交付 · 生成材料 · R20261123-1'


def test_copy_multiple_visible_projects_uses_list_order(project_list):
    view, copied = project_list
    view.setSelectionMode(QAbstractItemView.MultiSelection)
    view.item(2).setSelected(True)
    view.item(1).setSelected(True)
    view.item(1).setHidden(True)
    view.item(0).setSelected(True)

    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)

    assert copied == ['样例（C#）管理平台\n文档 2.0 分发工具']


def test_copy_without_a_selected_project_leaves_clipboard_alone(project_list):
    view, copied = project_list
    view.setCurrentRow(0)
    view.clearSelection()

    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
    assert copied == []

    view.clear()
    view.addItem('暂无项目')
    view.setCurrentRow(0)
    QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
    assert copied == []
