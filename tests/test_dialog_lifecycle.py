"""临时弹窗在读取结果后释放，取消不会写入业务配置。"""
import importlib

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget

from compact_ui import CompactPanel
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def flush_deletes():
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.fixture
def panel(qapp, tmp_path):
    value = CompactPanel(DesktopService(tmp_path / 'data/workbox.db'))
    yield value
    value.document_pane.shutdown()
    value.deleteLater()
    flush_deletes()


@pytest.mark.parametrize('entry', ['edit_directories', 'show_quick_start', 'show_intake_log', 'show_sources'])
def test_repeated_cancel_releases_temporary_dialogs_without_changing_settings(panel, qapp, entry):
    before = panel.service.workspaces.settings()
    for _ in range(3):
        QTimer.singleShot(0, lambda: qapp.activeModalWidget().reject())
        getattr(panel, entry)()
        flush_deletes()
        assert panel.findChildren(QDialog) == []
    assert panel.service.workspaces.settings() == before


def test_selected_project_is_consumed_before_dialog_destruction(panel, qapp):
    panel.service.receive_text('1. 隔离项目系统')
    project_id = panel.service.list_projects()[0]['id']
    def choose():
        dialog = qapp.activeModalWidget()
        dialog.findChild(QListWidget).setCurrentRow(0)
        dialog.accept()
    QTimer.singleShot(0, choose)
    assert panel.choose_file_project('未匹配文件.txt') == project_id
    flush_deletes()
    assert panel.findChildren(QDialog) == []


@pytest.mark.parametrize('raises', [False, True])
def test_transient_dialog_retains_fields_until_scope_exits_and_releases_on_error(qapp, raises):
    transient_dialog = importlib.import_module('dialog_lifecycle').transient_dialog
    dialog = QDialog()
    field = QLineEdit('保留结果', dialog)
    try:
        with transient_dialog(dialog):
            QTimer.singleShot(0, dialog.accept)
            assert dialog.exec() == QDialog.Accepted
            flush_deletes()
            assert field.text() == '保留结果'
            if raises:
                raise ValueError('隔离异常')
    except ValueError:
        assert raises
    flush_deletes()
    assert not shiboken6.isValid(dialog)
