"""数值控件的输入区域和 Fluent 按钮必须完整落在控件内。"""
import pytest
from PySide6.QtWidgets import QApplication

from compact_ui import DirectorySettingsDialog
from desktop_service import DesktopService
from onboarding_ui import FirstRunDialog
from theme import install_theme, set_theme


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('enabled', [False, True])
def test_spinbox_children_fit_real_dialogs(tmp_path, mode, enabled):
    app = QApplication.instance() or QApplication([])
    service = DesktopService(tmp_path / 'data/workbox.db')
    install_theme(service)
    set_theme(mode, service, persist=False)
    dialogs = [DirectorySettingsDialog(service), FirstRunDialog(service)]
    try:
        dialogs[1].set_step(1)
        for dialog in dialogs:
            dialog.show()
            dialog.resize(dialog.minimumSizeHint())
            dialog.zip_delivery_min_files.setEnabled(enabled)
            app.processEvents()
            spins = [dialog.zip_delivery_min_files]
            if isinstance(dialog, DirectorySettingsDialog):
                spins.append(dialog.stable_seconds)
            for spin in spins:
                for focused in (False, True):
                    if focused and spin.isEnabled():
                        spin.setFocus()
                    else:
                        spin.clearFocus()
                    app.processEvents()
                    for child in (spin.lineEdit(), spin.upButton, spin.downButton):
                        assert spin.rect().contains(child.geometry()), (mode, enabled, spin.size(), child.geometry())
                    assert spin.lineEdit().height() >= spin.fontMetrics().height()
                if spin.isEnabled():
                    value, suffix = spin.value(), spin.suffix()
                    spin.upButton.click()
                    assert spin.value() == value + spin.singleStep()
                    spin.downButton.click()
                    assert spin.value() == value and spin.suffix() == suffix
                set_theme('dark' if mode == 'light' else 'light', service, persist=False)
                app.processEvents()
                for child in (spin.lineEdit(), spin.upButton, spin.downButton):
                    assert spin.rect().contains(child.geometry())
                set_theme(mode, service, persist=False)
    finally:
        for dialog in dialogs:
            dialog.close()
        install_theme()
