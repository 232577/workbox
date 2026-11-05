"""归档分类规则可从设置进入，不能丢弃未保存目录变更。"""
from PySide6.QtWidgets import QApplication
from compact_ui import DirectorySettingsDialog
from desktop_service import DesktopService


def test_settings_entry_does_not_save_other_settings(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data/workbox.db')
    before = service.workspaces.settings()
    dialog = DirectorySettingsDialog(service)
    dialog.archive_rule_button.click()
    assert dialog.open_classification
    assert service.workspaces.settings() == before
    dialog.close()


def test_unsaved_settings_cannot_be_discarded_by_classification_entry(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data/workbox.db')
    dialog = DirectorySettingsDialog(service)
    dialog.work_root.setText(str(tmp_path / 'new-work'))
    assert not dialog.archive_rule_button.isEnabled()
    dialog.archive_rule_button.click()
    assert not dialog.open_classification
    dialog.close()
