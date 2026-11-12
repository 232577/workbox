"""Approved cube brand and logical-pixel floating-entry contracts."""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMenu

import branding
from floating import FloatingBall
from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def raster(widget):
    image = QImage(widget.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    widget.render(image)
    return image


def test_cube_has_three_gradient_faces_and_a_font_independent_white_w(qapp):
    vector = branding.svg_bytes().decode()
    assert all(f'id="{face}"' in vector for face in ('cube-top', 'cube-left', 'cube-right', 'cube-w'))
    assert '<text' not in vector and '<image' not in vector
    image = branding.icon_image(128)
    white = sum(1 for y in range(128) for x in range(128)
                if (c := image.pixelColor(x, y)).alpha() > 240
                and min(c.red(), c.green(), c.blue()) > 230)
    assert white > 128 * 128 * .015


def test_default_is_48_logical_pixels_and_hover_does_not_move_hit_target(qapp):
    orb = FloatingBall()
    assert orb.size().width() == orb.size().height() == 48
    before = raster(orb)
    point = QPointF(24, 24)
    QApplication.sendEvent(orb, QEnterEvent(point, point, point))
    assert raster(orb) != before
    assert orb.size().width() == 48
    QApplication.sendEvent(orb, QEvent(QEvent.Leave))
    assert raster(orb) == before


@pytest.mark.parametrize('size', [32, 40, 48, 56, 64])
def test_supported_sizes_render_and_keyboard_open_still_works(qapp, size):
    orb = FloatingBall()
    assert hasattr(orb, 'set_orb_size')
    orb.set_orb_size(size)
    assert orb.width() == orb.height() == size
    image = raster(orb)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(size // 2, size // 2).alpha() == 255
    opened = QSignalSpy(orb.open_requested)
    QTest.keyClick(orb, Qt.Key_Return)
    assert opened.count() == 1


def test_size_menu_tracks_external_changes_when_reopened(qapp):
    orb = FloatingBall()
    menu = orb.context_menu()
    # QAction.menu() through temporary wrappers can transfer ownership in
    # PySide6; inspect the actual QObject children without destroying the menu.
    submenus = menu.findChildren(QMenu)
    size_menu = next((m for m in submenus if m.title() == '悬浮窗尺寸'), None)
    assert size_menu is not None
    requested = QSignalSpy(orb.size_requested)
    sizes = {a.data(): a for a in size_menu.actions()}
    assert set(sizes) == {32, 40, 48, 56, 64}
    assert sizes[48].isChecked()
    sizes[64].trigger()
    assert requested.count() == 1 and requested.at(0) == [64]
    # Persistence belongs to the controller; the menu must not resize first.
    assert orb.width() == 48
    orb.set_orb_size(40)
    size_menu.aboutToShow.emit()
    assert [s for s, a in sizes.items() if a.isChecked()] == [40]


@pytest.mark.parametrize('size', [0, 31, 49, 76, 10000, True, 48.0, '48', None])
def test_invalid_sizes_are_rejected_without_writing(tmp_path, size):
    service = DesktopService(tmp_path / 'data/workbox.db')
    with pytest.raises(ValueError):
        service.set_setting('orb_size', size)
    assert service.get_setting('orb_size') is None


@pytest.mark.parametrize('size', [32, 40, 48, 56, 64])
def test_size_preference_round_trips_without_business_history(tmp_path, size):
    service = DesktopService(tmp_path / 'data/workbox.db')
    before = service.db.counts()
    service.set_setting('orb_size', size)
    assert DesktopService(tmp_path / 'data/workbox.db').get_setting('orb_size') == size
    assert service.db.counts() == before


def test_controller_restores_size_and_clamps_growth_at_display_edge(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.set_setting('orb_size', 40)
    control = FloatingController(service, tray_enabled=False)
    try:
        assert control.start()
        assert control.orb.width() == 40
        area = control.orb.screen().availableGeometry()
        control.orb.move(area.right() - 39, area.bottom() - 39)
        control.change_orb_size(64)
        assert control.orb.width() == 64
        assert service.get_setting('orb_size') == 64
        assert area.contains(control.orb.geometry())
    finally:
        control.shutdown()


def test_failed_size_save_keeps_previous_geometry(qapp, tmp_path, monkeypatch):
    service = DesktopService(tmp_path / 'data/workbox.db')
    control = FloatingController(service, tray_enabled=False)
    try:
        assert control.start()
        assert hasattr(control, 'change_orb_size')
        previous = control.orb.geometry()
        messages = []
        monkeypatch.setattr(control, 'notify', lambda text, **kwargs: messages.append(text))
        def fail(*_args):
            raise OSError('isolated persistence failure')
        monkeypatch.setattr(service, 'set_setting', fail)
        control.change_orb_size(64)
        assert control.orb.geometry() == previous
        assert messages
        monkeypatch.undo()
    finally:
        control.shutdown()


def test_corrupt_saved_size_uses_default_without_rewriting(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    with service.db.connect() as db:
        db.execute("INSERT INTO desktop_settings(key,value) VALUES ('orb_size','999')")
    control = FloatingController(service, tray_enabled=False)
    try:
        assert control.start()
        assert control.orb.width() == 48
        assert service.get_setting('orb_size') == 999
    finally:
        control.shutdown()


def test_exact_count_is_accessible_even_in_compact_mode(qapp):
    orb = FloatingBall()
    orb.set_summary(123)
    assert '123' in orb.accessibleDescription()
