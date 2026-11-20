"""Observe desktop changes without changing windows or application data."""

import shiboken6
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QSystemTrayIcon


class DesktopEnvironmentWatcher(QObject):
    display_changed = Signal()
    tray_lost = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._application = None
        self._application_connections = []
        self._screen_connections = {}
        self._background_tray = None
        self._tray_connection = None
        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.setInterval(125)
        self._display_timer.timeout.connect(self._emit_display_changed)
        self._tray_timer = QTimer(self)
        self._tray_timer.setInterval(2000)
        self._tray_timer.timeout.connect(self._check_tray)

    def start(self):
        if self._running:
            return
        application = QApplication.instance()
        if application is None:
            return
        self._application = application
        self._running = True
        self._application_connections = [
            application.screenAdded.connect(self._screens_changed),
            application.screenRemoved.connect(self._screens_changed),
        ]
        self._watch_screens()
        if self._background_tray is not None:
            self._tray_timer.start()

    def stop(self):
        self._running = False
        self._display_timer.stop()
        self.set_background_tray(None)
        self._disconnect(self._application_connections)
        self._application_connections = []
        for _screen, connections in self._screen_connections.values():
            self._disconnect(connections)
        self._screen_connections.clear()
        self._application = None

    def set_background_tray(self, tray):
        if tray is self._background_tray:
            return
        self._tray_timer.stop()
        if self._tray_connection is not None:
            self._disconnect([self._tray_connection])
            self._tray_connection = None
        self._background_tray = tray
        if tray is None:
            return
        if shiboken6.isValid(tray):
            self._tray_connection = tray.destroyed.connect(self._tray_destroyed)
        if self._running:
            self._tray_timer.start()

    @staticmethod
    def _disconnect(connections):
        # Connection handles remain safe when their QScreen/QObject is gone.
        for connection in connections:
            QObject.disconnect(connection)

    def _watch_screens(self):
        current = {id(screen): screen for screen in self._application.screens()
                   if shiboken6.isValid(screen)}
        for identity in self._screen_connections.keys() - current.keys():
            _screen, connections = self._screen_connections.pop(identity)
            self._disconnect(connections)
        for identity, screen in current.items():
            if identity not in self._screen_connections:
                self._screen_connections[identity] = (screen, [
                    screen.availableGeometryChanged.connect(self._queue_display_change),
                    screen.logicalDotsPerInchChanged.connect(self._queue_display_change),
                ])

    @Slot()
    def _screens_changed(self):
        if self._running:
            self._watch_screens()
            self._queue_display_change()

    @Slot()
    def _queue_display_change(self):
        if self._running:
            self._display_timer.start()

    @Slot()
    def _emit_display_changed(self):
        if self._running:
            self.display_changed.emit()

    @Slot()
    def _check_tray(self):
        tray = self._background_tray
        if not self._running or not self._tray_timer.isActive() or tray is None:
            return
        if (not shiboken6.isValid(tray)
                or not QSystemTrayIcon.isSystemTrayAvailable()
                or not tray.isVisible()):
            self._lose_tray()

    @Slot()
    def _tray_destroyed(self):
        if self._running and self._background_tray is not None:
            self._lose_tray()
        else:
            self.set_background_tray(None)

    def _lose_tray(self):
        self.set_background_tray(None)
        self.tray_lost.emit()
