"""首次设置与随时可读的使用指南，不改写已有项目或历史。"""
import importlib
import sqlite3

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from compact_ui import CompactPanel, DirectorySettingsDialog
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def onboarding():
    assert importlib.util.find_spec('onboarding_ui') is not None, '首次设置界面尚未实现'
    return importlib.import_module('onboarding_ui')


def finish_steps(dialog):
    dialog.next_button.click()
    dialog.next_button.click()
    dialog.finish_button.click()


def test_first_use_back_and_cancel_preserve_defaults_without_writes(qapp, service, tmp_path):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service)
    assert dialog.manual_mode.isChecked()
    assert dialog.work_root.text() == before['work_root']
    assert dialog.archive_root.text() == before['archive_root']
    dialog.work_root.setText(str(tmp_path / 'chosen-work'))
    dialog.next_button.click()
    dialog.automatic_mode.click()
    dialog.zip_delivery_min_files.setValue(7)
    dialog.back_button.click()
    assert dialog.work_root.text() == str(tmp_path / 'chosen-work')
    dialog.next_button.click()
    assert dialog.automatic_mode.isChecked() and dialog.zip_delivery_min_files.value() == 7
    dialog.cancel_button.click()
    assert dialog.result() == QDialog.Rejected
    assert service.workspaces.settings() == before
    assert not (tmp_path / 'chosen-work').exists()
    assert service.db.counts()['projects'] == 0


def test_first_use_finish_persists_manual_rule_and_completion(qapp, service):
    dialog = onboarding().FirstRunDialog(service)
    finish_steps(dialog)
    assert dialog.result() == QDialog.Accepted
    saved = service.workspaces.settings()
    assert saved['auto_deliver_zip'] is False
    assert saved['auto_create_projects'] is True
    assert saved['zip_delivery_min_files'] == 4
    assert saved['stable_seconds'] == 10
    assert service.workspaces.setup_required() is False
    assert service.db.counts()['projects'] == 0


def test_first_use_finish_persists_explicit_automatic_threshold(qapp, service):
    dialog = onboarding().FirstRunDialog(service)
    dialog.next_button.click()
    dialog.automatic_mode.click()
    dialog.zip_delivery_min_files.setValue(9)
    dialog.next_button.click()
    dialog.finish_button.click()
    assert dialog.result() == QDialog.Accepted
    saved = service.workspaces.settings()
    assert saved['auto_deliver_zip'] is True and saved['zip_delivery_min_files'] == 9


def test_invalid_paths_keep_editable_form_without_marking_complete(qapp, service):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service)
    dialog.archive_root.setText(dialog.work_root.text())
    finish_steps(dialog)
    assert dialog.result() != QDialog.Accepted
    assert dialog.error.text()
    assert dialog.archive_root.text() == dialog.work_root.text()
    assert service.workspaces.settings() == before
    assert service.workspaces.setup_required()
    dialog.reject()


def test_save_failure_keeps_input_and_allows_retry(qapp, service, monkeypatch):
    dialog = onboarding().FirstRunDialog(service)
    dialog.next_button.click()
    dialog.automatic_mode.click()
    dialog.zip_delivery_min_files.setValue(8)
    dialog.next_button.click()
    configure = service.workspaces.configure
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('isolated database is locked')
    monkeypatch.setattr(service.workspaces, 'configure', fail)
    dialog.finish_button.click()
    assert dialog.result() != QDialog.Accepted and 'locked' in dialog.error.text()
    assert dialog.automatic_mode.isChecked() and dialog.zip_delivery_min_files.value() == 8
    monkeypatch.setattr(service.workspaces, 'configure', configure)
    dialog.finish_button.click()
    assert dialog.result() == QDialog.Accepted


def test_settings_mode_presets_change_only_existing_delivery_checkbox(qapp, service):
    dialog = DirectorySettingsDialog(service)
    dialog.auto_create_projects.setChecked(False)
    dialog.zip_delivery_min_files.setValue(12)
    dialog.stable_seconds.setValue(30)
    roots = dialog.work_root.text(), dialog.archive_root.text()
    dialog.manual_mode_button.click()
    assert not dialog.auto_deliver_zip.isChecked()
    assert not dialog.zip_delivery_min_files.isEnabled()
    dialog.automatic_mode_button.click()
    assert dialog.auto_deliver_zip.isChecked() and dialog.zip_delivery_min_files.isEnabled()
    assert not dialog.auto_create_projects.isChecked()
    assert dialog.zip_delivery_min_files.value() == 12 and dialog.stable_seconds.value() == 30
    assert (dialog.work_root.text(), dialog.archive_root.text()) == roots
    dialog.reject()


@pytest.mark.parametrize('first_run', [True, False])
def test_runtime_save_failure_preserves_form_and_pending_setup(qapp, service, monkeypatch, first_run):
    before = service.workspaces.settings()
    dialog = onboarding().FirstRunDialog(service) if first_run else DirectorySettingsDialog(service)
    dialog.zip_delivery_min_files.setValue(11)
    if first_run:
        dialog.next_button.click()
        dialog.automatic_mode.click()
        dialog.next_button.click()
    def fail(*args, **kwargs):
        raise RuntimeError('isolated invalid setup state')
    monkeypatch.setattr(service.workspaces, 'configure', fail)
    dialog.save()
    assert dialog.result() != QDialog.Accepted
    assert 'invalid setup state' in dialog.error.text()
    assert dialog.zip_delivery_min_files.value() == 11
    assert service.workspaces.settings() == before
    assert service.workspaces.setup_required()
    dialog.reject()


def test_quick_guide_reads_actual_preferences_and_never_writes(qapp, service):
    current = service.workspaces.settings()
    service.workspaces.configure(current['work_root'], current['archive_root'], 15,
        auto_create_projects=False, auto_deliver_zip=False, zip_delivery_min_files=8)
    before = service.workspaces.settings()
    guide = onboarding().QuickStartDialog(service)
    assert '通用模式' in guide.mode_label.text()
    assert before['work_root'] in guide.work_root.text()
    assert before['archive_root'] in guide.archive_root.text()
    assert '关闭' in guide.auto_create_label.text()
    guide.close_button.click()
    assert service.workspaces.settings() == before
    service.workspaces.configure(before['work_root'], before['archive_root'], 15,
        auto_deliver_zip=True, zip_delivery_min_files=8)
    guide = onboarding().QuickStartDialog(service)
    assert '自动交付模式' in guide.mode_label.text() and '8' in guide.mode_label.text()
    guide.reject()


def test_panel_help_opens_real_read_only_guide(qapp, service):
    panel = CompactPanel(service)
    before = service.workspaces.settings()
    seen = []
    def close_guide():
        guide = QApplication.activeModalWidget()
        assert isinstance(guide, onboarding().QuickStartDialog)
        seen.append(guide.windowTitle())
        guide.close_button.click()
    QTimer.singleShot(0, close_guide)
    panel.help_button.click()
    assert seen and service.workspaces.settings() == before
    panel.close()
