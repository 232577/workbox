"""统一 Qt 标准控件、语义颜色和存活窗口的外观；不读取或刷新业务数据。"""
from __future__ import annotations

import os
import ctypes
import weakref
from functools import lru_cache

# QtPy 的首次绑定选择必须与 WorkBox 已使用的 PySide6 一致。
os.environ['QT_API'] = 'pyside6'

from PySide6.QtCore import QObject, Qt, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget
import shiboken6
import qdarkstyle
from qdarkstyle.dark.palette import DarkPalette
from qdarkstyle.light.palette import LightPalette


_MODES = ('light', 'dark', 'system')
_TOKENS = {
    'light': {
        'bg': '#F3F5F8', 'surface': '#FFFFFF', 'raised': '#EBF0F5',
        'ink': '#172B3A', 'muted': '#3D5266', 'border': '#CBD5DF', 'control_border': '#7890A1',
        'accent': '#0F766E', 'accent_hover': '#0B615A', 'accent_text': '#FFFFFF',
        'hover': '#E5F2EF', 'selection': '#0F766E', 'selection_text': '#FFFFFF',
        'disabled': '#657585', 'disabled_bg': '#E8EDF2',
        'scroll_handle': '#8395A5', 'scroll_hover': '#526575',
        'state_new': '#526575', 'state_pending': '#8A4A0C',
        'state_working': '#175EA8', 'state_delivered': '#0F766E',
        'error': '#AF303B', 'notice_bg': '#E5F2EF',
        'orb_bg': '#FFFFFF', 'orb_border': '#CBD5DF', 'orb_ink': '#172B3A',
        'orb_accent': '#0F766E', 'orb_glow': '#D2EAE4', 'orb_shadow': '#526575',
    },
    'dark': {
        'bg': '#181C24', 'surface': '#222833', 'raised': '#2B3340',
        'ink': '#F0F4F8', 'muted': '#B7C3CF', 'border': '#465365', 'control_border': '#64778B',
        'accent': '#69D5BE', 'accent_hover': '#86E2CE', 'accent_text': '#172B3A',
        'hover': '#2D4645', 'selection': '#2F8278', 'selection_text': '#FFFFFF',
        'disabled': '#93A1B0', 'disabled_bg': '#303947',
        'scroll_handle': '#6F8298', 'scroll_hover': '#A1B4C8',
        'state_new': '#B7C3CF', 'state_pending': '#F2BD73',
        'state_working': '#93C5FD', 'state_delivered': '#69D5BE',
        'error': '#FFACB4', 'notice_bg': '#263D3B',
        'orb_bg': '#222833', 'orb_border': '#465365', 'orb_ink': '#F0F4F8',
        'orb_accent': '#69D5BE', 'orb_glow': '#2D4645', 'orb_shadow': '#000000',
    },
}
_manager = None


def _valid_mode(mode):
    if not isinstance(mode, str) or mode not in _MODES:
        raise ValueError('外观必须为 light、dark 或 system。')
    return mode


def _system_resolved(scheme):
    return 'dark' if scheme == Qt.ColorScheme.Dark else 'light'


def current_mode():
    """用户选择的 light / dark / system；未安装主题时为 light。"""
    return _manager.mode if _manager is not None and shiboken6.isValid(_manager) else 'light'


def resolved_mode():
    """当前实际绘制的 light / dark。"""
    return _manager.resolved if _manager is not None and shiboken6.isValid(_manager) else 'light'


def _resolve(mode):
    if mode is None:
        return resolved_mode()
    _valid_mode(mode)
    if mode != 'system':
        return mode
    app = QApplication.instance()
    if _manager is not None and shiboken6.isValid(_manager):
        return _system_resolved(_manager.system_scheme)
    return _system_resolved(app.styleHints().colorScheme()) if app else 'light'


def tokens(mode=None):
    """返回颜色副本，调用方不能更改全局主题。"""
    return dict(_TOKENS[_resolve(mode)])


@lru_cache(maxsize=2)
def _base_stylesheet(mode):
    palette = DarkPalette if mode == 'dark' else LightPalette
    return qdarkstyle.load_stylesheet(qt_api='pyside6', palette=palette)


def stylesheet(mode=None, role='window'):
    """成熟标准控件主题之后追加 WorkBox 语义样式。"""
    actual = _resolve(mode)
    c = tokens(actual)
    rules = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 14px; color: %(ink)s; background: transparent; }
FloatingBall { background: transparent; border: none; }
QMainWindow, QDialog, QWidget#manager { background: %(bg)s; }
QWidget#surface, QFrame#card, QWidget#documentSurface, QWidget#projectSurface {
    background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px;
}
QWidget#toolbar, QToolBar { background: %(surface)s; border: none; border-bottom: 1px solid %(border)s; spacing: 6px; }
QStatusBar, QWidget#statusbar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QFrame#sidebar { background: %(raised)s; border: none; border-right: 1px solid %(border)s; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QLabel#heading, QLabel#pageTitle { font-size: 26px; font-weight: 700; }
QLabel#title, QLabel#sectionTitle { font-size: 18px; font-weight: 600; }
QLabel#subtle, QLabel#muted { font-size: 12px; color: %(muted)s; }
QLabel#brand { font-size: 24px; font-weight: 700; }
QLabel#brandMark { background: %(accent)s; color: %(accent_text)s; font-size: 27px; font-weight: 700; border-radius: 8px; }
QLabel#count, QLabel#emptyTitle { font-size: 22px; font-weight: 600; }
QLabel#notice { color: %(ink)s; background: %(notice_bg)s; border: 1px solid %(border)s; border-radius: 8px; padding: 8px 10px; }
QLabel#error { color: %(error)s; }
QPushButton, QToolButton {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(border)s;
    border-radius: 8px; padding: 7px 10px; min-height: 20px;
}
QPushButton:hover, QToolButton:hover { background: %(hover)s; border-color: %(accent)s; }
QPushButton:pressed, QToolButton:pressed { background: %(raised)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; padding: 6px 9px; }
QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: %(accent_text)s; border-color: %(accent)s; font-weight: 600; }
QPushButton#primary:hover, QPushButton#primary:pressed { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton#primary:disabled { background: %(disabled_bg)s; color: %(disabled)s; border-color: %(border)s; }
QPushButton#filter, QPushButton#nav { background: transparent; border: 1px solid transparent; }
QPushButton#nav { text-align: left; }
QPushButton#filter:hover, QPushButton#nav:hover { background: %(hover)s; }
QPushButton#filter:checked, QPushButton#nav:checked { background: %(selection)s; color: %(selection_text)s; font-weight: 600; }
QPushButton#filter:focus, QPushButton#nav:focus { border: 2px solid %(accent)s; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit, QTimeEdit {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(control_border)s; border-radius: 8px;
    padding: 7px; min-height: 20px; selection-background-color: %(selection)s; selection-color: %(selection_text)s;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {
    border: 2px solid %(accent)s; padding: 6px;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {
    color: %(disabled)s; background: %(disabled_bg)s;
}
QComboBox { padding-right: 28px; }
QComboBox::drop-down { width: 24px; border: none; background: transparent; }
QAbstractSpinBox { padding-right: 28px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { border: none; background: transparent; width: 24px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: %(hover)s; }
QComboBox QAbstractItemView { color: %(ink)s; background: %(surface)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; border: 1px solid %(border)s; }
QAbstractItemView, QListView, QListWidget, QTreeView, QTreeWidget, QTableView, QTableWidget {
    color: %(ink)s; background: %(surface)s; alternate-background-color: %(raised)s; border: 1px solid %(border)s;
    border-radius: 8px; gridline-color: %(border)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; outline: 0;
}
QListWidget#projectList, QTableWidget#documentTable { border: none; border-radius: 0; }
QAbstractItemView::item { padding: 8px 10px; border: none; }
QAbstractItemView::item:hover { background: %(hover)s; color: %(ink)s; }
QAbstractItemView::item:selected, QAbstractItemView::item:selected:hover,
QListView::item:selected:active, QListView::item:selected:!active,
QTreeView::item:selected:active, QTreeView::item:selected:!active,
QTableView::item:selected:active, QTableView::item:selected:!active {
    background: %(selection)s; color: %(selection_text)s;
}
QListView::item:!selected:hover, QTreeView::item:!selected:hover,
QTableView::item:!selected:hover { background: %(hover)s; color: %(ink)s; }
QAbstractItemView::item:focus { border: 1px solid %(accent)s; }
QHeaderView { color: %(muted)s; background: %(raised)s; }
QHeaderView::section, QTableCornerButton::section {
    background: %(raised)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s; padding: 9px; font-weight: 600;
}
QHeaderView::section:horizontal, QHeaderView::section::horizontal,
QHeaderView::section:horizontal:first, QHeaderView::section:horizontal:only-one,
QHeaderView::section::horizontal::first, QHeaderView::section::horizontal::only-one {
    background: %(raised)s; color: %(muted)s; border: none; border-radius: 0;
    border-bottom: 1px solid %(border)s; padding: 9px 10px; min-height: 18px;
    font-size: 12px; font-weight: 600;
}
QHeaderView::section:vertical, QHeaderView::section::vertical,
QHeaderView::section:vertical:first, QHeaderView::section:vertical:only-one,
QHeaderView::section::vertical::first, QHeaderView::section::vertical::only-one {
    background: %(raised)s; color: %(muted)s; border: none; border-radius: 0;
    border-right: 1px solid %(border)s; padding: 9px 10px; min-height: 18px;
    font-size: 12px; font-weight: 600;
}
QHeaderView::section:horizontal:hover, QHeaderView::section::horizontal:hover,
QHeaderView::section:horizontal:pressed, QHeaderView::section::horizontal:pressed,
QHeaderView::section:vertical:hover, QHeaderView::section::vertical:hover,
QHeaderView::section:vertical:pressed, QHeaderView::section::vertical:pressed {
    background: %(hover)s; color: %(ink)s;
}
QHeaderView::section:horizontal:disabled, QHeaderView::section::horizontal:disabled,
QHeaderView::section:vertical:disabled, QHeaderView::section::vertical:disabled { color: %(disabled)s; }
QHeaderView::up-arrow, QHeaderView::down-arrow { background: transparent; border: none; }
QTabWidget::pane { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; }
QTabBar::tab { color: %(muted)s; background: %(bg)s; border: none; border-bottom: 2px solid transparent; padding: 10px 16px; }
QTabBar::tab:hover { background: %(hover)s; color: %(ink)s; }
QTabBar::tab:selected { background: %(surface)s; color: %(accent)s; border-bottom: 2px solid %(accent)s; font-weight: 600; }
QTabBar::tab:disabled { color: %(disabled)s; }
QTabBar::tab:top, QDockWidget QTabBar::tab:top,
QTabBar::tab:top:!selected, QDockWidget QTabBar::tab:top:!selected {
    color: %(muted)s; background: %(bg)s; border: none; border-radius: 0;
    border-bottom: 2px solid transparent; margin: 0; padding: 10px 16px;
    min-width: 54px; min-height: 18px;
}
QTabBar::tab:top:selected, QDockWidget QTabBar::tab:top:selected {
    color: %(accent)s; background: %(surface)s; border: none; border-radius: 0;
    border-bottom: 2px solid %(accent)s; margin: 0; padding: 10px 16px; font-weight: 600;
}
QTabBar::tab:top:!selected:hover, QDockWidget QTabBar::tab:top:!selected:hover {
    color: %(ink)s; background: %(hover)s; border: none;
    border-bottom: 2px solid transparent; margin: 0; padding: 10px 16px;
}
QTabBar::tab:top:selected:hover, QDockWidget QTabBar::tab:top:selected:hover {
    color: %(accent)s; background: %(hover)s; border: none; border-bottom: 2px solid %(accent)s;
}
QTabBar::tab:top:selected:disabled, QDockWidget QTabBar::tab:top:selected:disabled,
QTabBar::tab:top:!selected:disabled, QDockWidget QTabBar::tab:top:!selected:disabled {
    color: %(disabled)s; background: %(bg)s; border: none;
    border-bottom: 2px solid %(border)s; margin: 0; padding: 10px 16px;
}
QMenu, QMenuBar { color: %(ink)s; background: %(surface)s; border: 1px solid %(border)s; }
QMenu { border-radius: 8px; padding: 5px; }
QMenu::item { padding: 7px 28px 7px 12px; margin: 1px; border: none; border-radius: 5px; }
QMenu::item:selected, QMenuBar::item:selected { color: %(selection_text)s; background: %(selection)s; }
QMenu::item:disabled { color: %(disabled)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }
QToolTip { color: %(ink)s; background: %(surface)s; border: 1px solid %(border)s; padding: 6px 8px; }
QCheckBox, QRadioButton { spacing: 7px; }
QCheckBox:disabled, QRadioButton:disabled { color: %(disabled)s; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border: 2px solid %(accent)s; }
QGroupBox { color: %(ink)s; border: 1px solid %(border)s; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QCalendarWidget QWidget { background: %(surface)s; color: %(ink)s; }
QCalendarWidget QToolButton { color: %(ink)s; background: %(surface)s; border: none; border-radius: 5px; }
QCalendarWidget QToolButton:hover { background: %(hover)s; }
QCalendarWidget QAbstractItemView { color: %(ink)s; background: %(surface)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 14px; background: %(bg)s; margin: 14px 0; border: none; }
QScrollBar:horizontal { height: 14px; background: %(bg)s; margin: 0 14px; border: none; }
QScrollBar::handle:vertical { background: %(scroll_handle)s; border-radius: 5px; min-height: 32px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(scroll_handle)s; border-radius: 5px; min-width: 32px; margin: 2px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: %(scroll_hover)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 14px; background: %(bg)s; border: none; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 14px; background: %(bg)s; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: %(border)s; }
QProgressBar { background: %(raised)s; color: %(ink)s; border: 1px solid %(border)s; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QSlider::groove:horizontal { background: %(border)s; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: %(accent)s; border: 1px solid %(accent)s; border-radius: 7px; width: 14px; margin: -5px 0; }
FeedbackBubble { background: %(surface)s; color: %(ink)s; border: 1px solid %(border)s; border-radius: 8px; }
FeedbackBubble QLabel { background: transparent; color: %(ink)s; }
QWidget#manager[dense="true"] QPushButton, QWidget#manager[dense="true"] QToolButton {
    padding: 4px 8px; min-height: 18px; font-size: 12px;
}
QWidget#manager[dense="true"] QPushButton:focus, QWidget#manager[dense="true"] QToolButton:focus {
    padding: 3px 7px;
}
QWidget#manager[dense="true"] QLineEdit, QWidget#manager[dense="true"] QComboBox,
QWidget#manager[dense="true"] QDateEdit, QWidget#manager[dense="true"] QDateTimeEdit,
QWidget#manager[dense="true"] QTimeEdit, QWidget#manager[dense="true"] QSpinBox,
QWidget#manager[dense="true"] QDoubleSpinBox { padding: 4px; min-height: 18px; font-size: 12px; }
QWidget#manager[dense="true"] QLineEdit:focus, QWidget#manager[dense="true"] QComboBox:focus,
QWidget#manager[dense="true"] QDateEdit:focus, QWidget#manager[dense="true"] QDateTimeEdit:focus,
QWidget#manager[dense="true"] QTimeEdit:focus, QWidget#manager[dense="true"] QSpinBox:focus,
QWidget#manager[dense="true"] QDoubleSpinBox:focus { padding: 3px; }
QWidget#manager[dense="true"] QComboBox, QWidget#manager[dense="true"] QDateEdit,
QWidget#manager[dense="true"] QDateTimeEdit, QWidget#manager[dense="true"] QTimeEdit,
QWidget#manager[dense="true"] QSpinBox, QWidget#manager[dense="true"] QDoubleSpinBox { padding-right: 26px; }
QWidget#manager[dense="true"] QComboBox:focus, QWidget#manager[dense="true"] QDateEdit:focus,
QWidget#manager[dense="true"] QDateTimeEdit:focus, QWidget#manager[dense="true"] QTimeEdit:focus,
QWidget#manager[dense="true"] QSpinBox:focus, QWidget#manager[dense="true"] QDoubleSpinBox:focus { padding-right: 25px; }
QWidget#manager[dense="true"] QTabBar::tab:top { padding: 6px 10px; font-size: 12px; }
QWidget#manager[dense="true"] QLabel#title, QWidget#manager[dense="true"] QLabel#sectionTitle { font-size: 15px; }
QWidget#manager[dense="true"] QLabel#notice { padding: 5px 8px; font-size: 12px; }
""" % c
    if role in ('feedback', 'bubble'):
        rules += ('\nQWidget#feedbackBubble { background: %(surface)s; color: %(ink)s; '
                  'border: 1px solid %(border)s; border-radius: 8px; }\n') % c
    base = _base_stylesheet(actual) if QApplication.instance() is not None else ''
    return base + '\n/* WorkBox semantic appearance */\n' + rules


def _palette(mode):
    c = tokens(mode)
    palette = QPalette()
    roles = {
        QPalette.Window: 'bg', QPalette.WindowText: 'ink', QPalette.Base: 'surface',
        QPalette.AlternateBase: 'raised', QPalette.ToolTipBase: 'surface',
        QPalette.ToolTipText: 'ink', QPalette.Text: 'ink', QPalette.Button: 'surface',
        QPalette.ButtonText: 'ink', QPalette.BrightText: 'selection_text',
        QPalette.Highlight: 'selection', QPalette.HighlightedText: 'selection_text',
        QPalette.Link: 'accent', QPalette.LinkVisited: 'accent',
        QPalette.Light: 'raised', QPalette.Midlight: 'raised', QPalette.Mid: 'border',
        QPalette.Dark: 'border', QPalette.Shadow: 'border', QPalette.PlaceholderText: 'muted',
    }
    for role, key in roles.items():
        palette.setColor(role, QColor(c[key]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText, QPalette.PlaceholderText):
        palette.setColor(QPalette.Disabled, role, QColor(c['disabled']))
    palette.setColor(QPalette.Disabled, QPalette.Button, QColor(c['disabled_bg']))
    palette.setColor(QPalette.Disabled, QPalette.Base, QColor(c['disabled_bg']))
    return palette


class ThemeManager(QObject):
    changed = Signal(str)

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.service = None
        self.mode = 'light'
        self.resolved = 'light'
        self.system_scheme = app.styleHints().colorScheme()
        self._widgets = weakref.WeakKeyDictionary()
        self._scrollbars = weakref.WeakSet()
        app.styleHints().colorSchemeChanged.connect(self._on_system_color_scheme_changed)
        app.installEventFilter(self)
        app.setStyle('Fusion')
        app.setFont(QFont('Microsoft YaHei UI', 10))
        self._apply()

    def _style_native_window(self, widget):
        # 仅调整本进程Qt窗口的系统标题栏；不支持这些属性的平台保持系统默认。
        if (os.name != 'nt' or self.app.platformName() != 'windows'
                or not isinstance(widget, QWidget) or not widget.isWindow()
                or widget.windowFlags() & Qt.FramelessWindowHint or widget.windowHandle() is None):
            return
        try:
            dwm = ctypes.WinDLL('dwmapi')
            setter = dwm.DwmSetWindowAttribute
            setter.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            setter.restype = ctypes.c_long
            hwnd = ctypes.c_void_p(int(widget.winId()))
            dark = ctypes.c_int(int(self.resolved == 'dark'))
            setter(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            colors = tokens(self.resolved)
            for attribute, key in ((35, 'bg'), (36, 'ink')):
                color = QColor(colors[key])
                value = ctypes.c_uint(color.red() | color.green() << 8 | color.blue() << 16)
                setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, AttributeError):
            pass

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.WinIdChange):
            self._style_native_window(watched)
        return False

    def _on_system_color_scheme_changed(self, scheme):
        self.system_scheme = scheme
        if self.mode == 'system':
            next_mode = _system_resolved(scheme)
            if next_mode != self.resolved:
                self.resolved = next_mode
                self._apply()
                self.changed.emit(self.resolved)

    def _apply_scrollbar(self, bar):
        c = tokens(self.resolved)
        palette = bar.palette()
        for role, key in ((QPalette.Button, 'scroll_handle'), (QPalette.ButtonText, 'ink'),
                          (QPalette.Dark, 'scroll_hover'), (QPalette.Shadow, 'border'),
                          (QPalette.Window, 'bg')):
            palette.setColor(role, QColor(c[key]))
        bar.setPalette(palette)
        bar.update()

    def _apply(self):
        # 设置应用级样式与 palette，使无父级菜单、系统提示也得到同一主题。
        self.app.setStyleSheet(stylesheet(self.resolved))
        self.app.setPalette(_palette(self.resolved))
        for widget, role in list(self._widgets.items()):
            if shiboken6.isValid(widget):
                self._style_native_window(widget)
                widget.update()
            else:
                self._widgets.pop(widget, None)
        for bar in list(self._scrollbars):
            if shiboken6.isValid(bar):
                self._apply_scrollbar(bar)
            else:
                self._scrollbars.discard(bar)
        for window in self.app.topLevelWidgets():
            self._style_native_window(window)

    def set_theme(self, mode, service=None, persist=True):
        _valid_mode(mode)
        target_service = self.service if service is None else service
        # 数据保存必须先完成，失败时不改变模式、palette、窗口或发信号。
        if persist and target_service is not None:
            if target_service.set_setting('appearance_theme', mode) is False:
                raise RuntimeError('外观设置未能保存。')
        if service is not None:
            self.service = service
        previous = (self.mode, self.resolved)
        self.mode = mode
        self.resolved = _system_resolved(self.system_scheme) if mode == 'system' else mode
        if previous != (self.mode, self.resolved):
            self._apply()
            self.changed.emit(self.resolved)
        return True


def manager():
    global _manager
    app = QApplication.instance()
    if app is None:
        raise RuntimeError('请先创建 QApplication，再安装主题。')
    if _manager is None or not shiboken6.isValid(_manager):
        _manager = ThemeManager(app)
    return _manager


def install_theme(service=None):
    """读取已保存偏好，不新增、迁移或覆盖任何设置。"""
    value = service.get_setting('appearance_theme', 'light') if service is not None else 'light'
    mode = value if isinstance(value, str) and value in _MODES else 'light'
    instance = manager()
    instance.service = service
    instance.set_theme(mode, persist=False)
    return instance


def set_theme(mode, service=None, persist=True):
    return manager().set_theme(mode, service, persist)


def apply_to(widget, role='window'):
    """注册窗口并覆盖旧局部样式，切换时保持当前控件与业务状态。"""
    instance = manager()
    if shiboken6.isValid(widget):
        instance._widgets[widget] = role
        # 应用级皮肤只解析一次；局部大样式会让大量窗口切换时重复重绘。
        if widget.styleSheet():
            widget.setStyleSheet('')
        if role in ('feedback', 'bubble'):
            widget.setObjectName('feedbackBubble')
        widget.ensurePolished()
        # dense等祖先动态属性改变后，让现有控件重算外观，不重建内容。
        for child in [widget, *widget.findChildren(QWidget)]:
            if shiboken6.isValid(child):
                child.style().unpolish(child)
                child.style().polish(child)
        widget.update()
    return widget


def apply_scrollbar(bar):
    instance = manager()
    if shiboken6.isValid(bar):
        instance._scrollbars.add(bar)
        instance._apply_scrollbar(bar)
    return bar


def theme_signal():
    return manager().changed
