import pytest
from PySide6.QtWidgets import QApplication

from desktop_controller import FloatingController
from desktop_service import DesktopService


@pytest.fixture(scope='module')
def qapp():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def test_setup_holds_session_lock_before_any_intake_or_monitor(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    owner = FloatingController(service, tray_enabled=False)
    other = FloatingController(service, tray_enabled=False)
    called = []
    def setup():
        assert owner.owns_lock
        assert owner.panel is None and owner.orb is None
        assert not owner.monitor_timer.isActive()
        assert not other.start(setup_callback=lambda: called.append('second'))
        assert not called
        roots = service.workspaces.settings()
        service.workspaces.configure(roots['work_root'], roots['archive_root'],
                                    auto_deliver_zip=False, complete_setup=True)
        return True
    try:
        assert owner.start(setup_callback=setup)
        assert owner.monitor_timer.isActive()
        assert owner.panel is not None
        assert service.workspaces.settings()['auto_deliver_zip'] is False
        assert not service.workspaces.setup_required()
    finally:
        other.shutdown()
        owner.shutdown()


def test_cancel_setup_releases_lock_and_does_not_start_background_work(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    original = service.workspaces.settings()
    controller = FloatingController(service, tray_enabled=False)
    try:
        assert not controller.start(setup_callback=lambda: False)
        assert not controller.owns_lock
        assert controller.panel is None and controller.orb is None
        assert not controller.monitor_timer.isActive()
        assert service.workspaces.settings() == original
        assert service.workspaces.setup_required()
    finally:
        controller.shutdown()


def test_completed_setup_never_calls_wizard_or_changes_custom_rules(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.workspaces.configure(tmp_path / 'work', tmp_path / 'archive', 37,
                                auto_create_projects=False, auto_deliver_zip=True,
                                zip_delivery_min_files=9, complete_setup=True)
    original = service.workspaces.settings()
    controller = FloatingController(service, tray_enabled=False)
    def unexpected():
        raise AssertionError('Existing installation must not show setup')
    try:
        assert controller.start(setup_callback=unexpected)
        assert service.workspaces.settings() == original
    finally:
        controller.shutdown()


def test_uncommitted_setup_cannot_report_success(qapp, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    controller = FloatingController(service, tray_enabled=False)
    try:
        with pytest.raises(ValueError, match='首次设置'):
            controller.start(setup_callback=lambda: True)
        assert controller.panel is None
        assert not controller.monitor_timer.isActive()
    finally:
        controller.shutdown()
