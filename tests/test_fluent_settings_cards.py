"""设置卡片保留真实表单事务、访问名称和窄窗滚动。"""
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QApplication, QScrollArea
from qfluentwidgets import CardWidget, SettingCard, SettingCardGroup

from compact_ui import DirectorySettingsDialog
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_settings_use_real_cards_and_keep_footer_visible(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    dialog = DirectorySettingsDialog(service)
    dialog.resize(640, 540)
    dialog.show()
    qapp.processEvents()
    groups = dialog.findChildren(SettingCardGroup)
    assert [group.titleLabel.text() for group in groups] == ['工作目录', '收录与交付', '归档', '项目类型']
    assert dialog.findChildren(SettingCard)
    assert dialog.findChildren(CardWidget)
    scroll = dialog.findChild(QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    for value in (0, scroll.verticalScrollBar().maximum()):
        scroll.verticalScrollBar().setValue(value)
        qapp.processEvents()
        for control in (dialog.save_button, dialog.cancel_button, dialog.reset_rules_button):
            top = control.mapTo(dialog, QPoint(0, 0))
            assert dialog.rect().contains(top)
            assert top.y() + control.height() <= dialog.height()
    for checkbox in (dialog.auto_create_projects, dialog.auto_deliver_zip):
        assert QAccessible.queryAccessibleInterface(checkbox).role() == QAccessible.CheckBox
    assert dialog.work_root.accessibleName() == '项目工作根目录'
    assert dialog.archive_root.accessibleName() == 'ZIP 归档根目录'


def test_settings_cancel_does_not_persist_cards_and_save_roundtrips_all_fields(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    before = service.workspaces.settings()
    dialog = DirectorySettingsDialog(service)
    dialog.work_root.setText(str(tmp_path / '新工作目录'))
    dialog.archive_root.setText(str(tmp_path / '新归档目录'))
    dialog.auto_create_projects.setChecked(False)
    dialog.auto_deliver_zip.setChecked(False)
    dialog.zip_delivery_min_files.setValue(11)
    dialog.stable_seconds.setValue(37)
    dialog.cancel_button.click()
    assert service.workspaces.settings() == before
    dialog.show()
    dialog.save_button.click()
    after = service.workspaces.settings()
    assert after['work_root'] == str(tmp_path / '新工作目录')
    assert after['archive_root'] == str(tmp_path / '新归档目录')
    assert after['auto_create_projects'] is False
    assert after['auto_deliver_zip'] is False
    assert after['zip_delivery_min_files'] == 11
    assert after['stable_seconds'] == 37
