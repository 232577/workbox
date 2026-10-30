import pytest
import os
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import install_theme, set_theme


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('width', [700, 1100])
def test_brand_icon_retains_complete_pixmap(tmp_path, mode, width):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data/workbox.db')
    install_theme(service)
    set_theme(mode, service, persist=False)
    panel = CompactPanel(service)
    try:
        panel.resize(width, 700)
        panel.show()
        app.processEvents()
        evidence = Path('.local/brand-icon-evidence')
        evidence.mkdir(parents=True, exist_ok=True)
        assert panel.grab().save(str(evidence / f"{mode}-{width}-{os.environ.get('QT_SCALE_FACTOR', '1')}x.png"))
        label = panel.brand_icon
        assert label.contentsRect().width() == 32
        assert label.contentsRect().height() == 32
        reference = QLabel(panel)
        reference.setStyleSheet('padding: 0px; border: 0px; background: transparent;')
        reference.setFixedSize(32, 32)
        reference.setPixmap(label.pixmap())
        reference.show()
        app.processEvents()
        assert label.grab().toImage() == reference.grab().toImage()
    finally:
        panel.document_pane.shutdown()
        panel.close()
        install_theme()
