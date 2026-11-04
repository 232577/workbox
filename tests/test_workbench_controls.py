"""Visible saved states remain distinguishable after the control redesign."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QVBoxLayout, QWidget

from theme import install_theme, set_theme
from ui_components import CheckBox, button


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def luminance(color):
    values = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
              for v in (color.redF(), color.greenF(), color.blueF())]
    return sum(v * weight for v, weight in zip(values, (.2126, .7152, .0722)))


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('state', [Qt.Checked, Qt.PartiallyChecked])
def test_disabled_checkbox_still_exposes_its_saved_state(qapp, mode, state):
    install_theme()
    set_theme(mode, persist=False)
    check = CheckBox('Saved option')
    check.setTristate(True)
    check.setCheckState(state)
    check.setEnabled(False)
    check.show()
    qapp.processEvents()
    try:
        option = QStyleOptionButton()
        option.initFrom(check)
        rect = check.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, check).adjusted(4, 4, -4, -4)
        image = check.grab().toImage()
        scale = image.devicePixelRatio()
        values = [luminance(image.pixelColor(round(x * scale), round(y * scale)))
                  for x in range(rect.left(), rect.right() + 1)
                  for y in range(rect.top(), rect.bottom() + 1)]
        assert (max(values) + .05) / (min(values) + .05) >= 3
        assert check.checkState() == state
    finally:
        check.close()
        check.deleteLater()
        set_theme('light', persist=False)


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_standard_navigation_keeps_a_visible_selected_page(qapp, mode):
    install_theme()
    set_theme(mode, persist=False)
    host = QWidget()
    layout = QVBoxLayout(host)
    nav = button('Projects', 'nav')
    layout.addWidget(nav)
    host.show()
    qapp.processEvents()
    try:
        samples = []
        for selected in (False, True):
            nav.setChecked(selected)
            nav.clearFocus()
            qapp.processEvents()
            image = host.grab().toImage()
            scale = image.devicePixelRatio()
            samples.append(image.pixelColor(round((nav.x() + nav.width() - 8) * scale),
                                            round((nav.y() + nav.height() // 2) * scale)))
        assert samples[0] != samples[1]
        assert nav.isChecked()
    finally:
        host.close()
        host.deleteLater()
        set_theme('light', persist=False)
