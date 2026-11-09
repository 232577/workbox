"""统一 Qt 标准控件、语义颜色和存活窗口的外观；不读取或刷新业务数据。"""
from __future__ import annotations

import os
import ctypes
import weakref
from functools import lru_cache

# QtPy 的首次绑定选择必须与 WorkBox 已使用的 PySide6 一致。
os.environ['QT_API'] = 'pyside6'

from PySide6.QtCore import QObject, Qt, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QCalendarWidget, QWidget
import shiboken6
import qdarkstyle
from qdarkstyle.dark.palette import DarkPalette
from qdarkstyle.light.palette import LightPalette


_MODES = ('light', 'dark', 'system')
_UI_FONT_FAMILIES = (
    'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC',
    'Noto Sans CJK SC', 'Source Han Sans SC', 'SimHei', 'WenQuanYi Micro Hei',
    'Segoe UI', 'Noto Sans', 'DejaVu Sans',
)
_TOKENS = {
    'light': {
        'bg': '#F1F3F5', 'surface': '#FCFDFE', 'raised': '#E7EBEF',
        'ink': '#222B35', 'muted': '#46515E', 'placeholder': '#5C6773',
        'border': '#D7DEE5', 'control_border': '#88939E', 'button_border': '#C5CED7',
        'accent': '#096C65', 'accent_hover': '#075D57', 'accent_pressed': '#054B47', 'accent_text': '#FFFFFF',
        'hover': '#EDF1F5', 'selection': '#086E68', 'selection_text': '#FFFFFF',
        'row_selected': '#E5EBF1', 'row_selected_border': '#BECBD7',
        'disabled': '#64707C', 'disabled_bg': '#EDF0F3', 'disabled_check': '#536170',
        'scroll_handle': '#87939F', 'scroll_hover': '#5B6976',
        'state_new': '#536170', 'state_pending': '#8A4A0C',
        'state_working': '#195BA3', 'state_delivered': '#096C65',
        'error': '#AF303B', 'notice_bg': '#E4F0EF',
        'subtle_surface': '#F6F8FA', 'info_bg': '#EDF4FB',
        'success_bg': '#E8F5EF', 'warning_bg': '#FFF4E3', 'error_bg': '#FDEDF0',
        'orb_bg': '#FFFFFF', 'orb_border': '#CBD5DF', 'orb_ink': '#172B3A',
        'orb_accent': '#0F766E', 'orb_glow': '#D2EAE4', 'orb_shadow': '#526575',
        'nav_bg': '#E7EBEF', 'nav_ink': '#27323E', 'nav_muted': '#46515E',
        'nav_hover': '#DCE3EA', 'nav_selected': '#F8FAFB', 'nav_accent': '#085E58',
    },
    'dark': {
        'bg': '#181A1C', 'surface': '#23292F', 'raised': '#303941',
        'ink': '#EEF1F5', 'muted': '#C4CCD5', 'placeholder': '#A7B2BE',
        'border': '#38424D', 'control_border': '#737F8D', 'button_border': '#4A5663',
        'accent': '#76CFC1', 'accent_hover': '#91DACE', 'accent_pressed': '#62B9AD', 'accent_text': '#17282B',
        'hover': '#2C343D', 'selection': '#307D78', 'selection_text': '#FFFFFF',
        'row_selected': '#323D47', 'row_selected_border': '#4E606F',
        'disabled': '#A0ACB9', 'disabled_bg': '#2B333B', 'disabled_check': '#A0ACB9',
        'scroll_handle': '#778592', 'scroll_hover': '#A0ACB9',
        'state_new': '#C4CCD5', 'state_pending': '#F2BD73',
        'state_working': '#9BC9FA', 'state_delivered': '#76CFC1',
        'error': '#FFACB4', 'notice_bg': '#28413F',
        'subtle_surface': '#20262D', 'info_bg': '#263748',
        'success_bg': '#203D36', 'warning_bg': '#433424', 'error_bg': '#442B35',
        'orb_bg': '#222833', 'orb_border': '#465365', 'orb_ink': '#F0F4F8',
        'orb_accent': '#69D5BE', 'orb_glow': '#2D4645', 'orb_shadow': '#000000',
        'nav_bg': '#1C2229', 'nav_ink': '#E3E9EF', 'nav_muted': '#C4CCD5',
        'nav_hover': '#2C343D', 'nav_selected': '#303941', 'nav_accent': '#91DACF',
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
    from studio_design import palette_overrides
    actual = _resolve(mode)
    return dict(_TOKENS[actual], **palette_overrides(actual))


def ui_font_families():
    """Qt 和 Fluent 使用相同的已安装字体序列，中文无衬线优先。"""
    if QApplication.instance() is None:
        return list(_UI_FONT_FAMILIES)
    installed = set(QFontDatabase.families())
    if not installed:  # Offscreen can lack a platform font database entirely.
        return list(_UI_FONT_FAMILIES)
    families = [family for family in _UI_FONT_FAMILIES if family in installed]
    return families or QFontDatabase.systemFont(QFontDatabase.GeneralFont).families()


@lru_cache(maxsize=2)
def _base_stylesheet(mode):
    palette = DarkPalette if mode == 'dark' else LightPalette
    return qdarkstyle.load_stylesheet(qt_api='pyside6', palette=palette)


def stylesheet(mode=None, role='window'):
    """成熟标准控件主题之后追加 WorkBox 语义样式。"""
    actual = _resolve(mode)
    c = tokens(actual)
    c['ui_fonts'] = ', '.join(f"'{family}'" for family in ui_font_families())
    from qfluentwidgets import FluentIcon, Theme
    icon_theme = Theme.DARK if actual == 'dark' else Theme.LIGHT
    c['calendar_icon'] = FluentIcon.CALENDAR.path(icon_theme)
    c['left_icon'] = FluentIcon.LEFT_ARROW.path(icon_theme)
    c['right_icon'] = FluentIcon.RIGHT_ARROW.path(icon_theme)
    c['chevron_icon'] = FluentIcon.ARROW_DOWN.path(icon_theme)
    rules = """
QWidget { font-family: %(ui_fonts)s; font-size: 14px; color: %(ink)s; background: transparent; }
FloatingBall { background: transparent; border: none; }
QMainWindow, QDialog, QWidget#manager { background: %(bg)s; }
QWidget#surface, QFrame#card, QWidget#documentSurface, QWidget#projectSurface {
    background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px;
}
QWidget#toolbar, QToolBar { background: %(surface)s; border: none; border-bottom: 1px solid %(border)s; spacing: 6px; }
QStatusBar, QWidget#statusbar { background: %(surface)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QFrame#sidebar { background: %(raised)s; border: none; border-right: 1px solid %(border)s; }
QTabWidget#workbenchPages::pane { background: transparent; border: none; }
QLabel, QCheckBox, QRadioButton { background: transparent; }
QFrame#dialogActions { background: transparent; border: none; border-top: 1px solid %(border)s; }
QFrame#dialogSection { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#pendingSettings { color: %(muted)s; font-size: 12px; }
QLabel#settingsNoResults { color: %(muted)s; background: %(subtle_surface)s; border-radius: 8px; padding: 18px; }
QLabel#heading, QLabel#pageTitle { font-size: 20px; font-weight: 600; }
QLabel#title, QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#subtle, QLabel#muted { font-size: 12px; color: %(muted)s; }
QLabel#brand { font-size: 24px; font-weight: 700; }
QLabel#brandName { font-size: 18px; font-weight: 700; }
QLabel#brandMark { background: %(accent)s; color: %(accent_text)s; font-size: 27px; font-weight: 700; border-radius: 8px; }
QLabel#count, QLabel#emptyTitle { font-size: 22px; font-weight: 600; }
QLabel#notice { color: %(ink)s; background: %(notice_bg)s; border: 1px solid %(border)s; border-radius: 8px; padding: 8px 10px; }
QLabel#error { color: %(error)s; }
QPushButton, QToolButton {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s;
    border-radius: 6px; padding: 6px 10px; min-height: 18px;
}
/* QDarkStyle 的状态规则自带 2px padding。显式覆盖，避免 Qt 在禁用或选中时
   缓存较小 sizeHint，恢复普通状态后用较大的内边距裁掉按钮文字。 */
QPushButton:disabled, QPushButton:checked, QPushButton:checked:disabled {
    padding: 6px 10px;
}
QPushButton:hover, QToolButton:hover { background: %(hover)s; border-color: %(control_border)s; }
QPushButton:pressed, QToolButton:pressed { background: %(raised)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; padding: 5px 9px; }
QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: %(accent_text)s; border-color: %(accent)s; font-weight: 600; }
QPushButton#primary:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton#primary:pressed { background: %(accent_pressed)s; border-color: %(accent_pressed)s; }
QPushButton#primary:focus { border-color: %(ink)s; }
QPushButton#primary:disabled { background: %(disabled_bg)s; color: %(disabled)s; border-color: %(border)s; }
QPushButton#filter, QPushButton#nav { background: transparent; border: 1px solid transparent; }
QPushButton#nav { text-align: left; }
QToolButton#nav { background: transparent; border: 1px solid transparent; text-align: left; padding: 7px 10px 7px 8px; }
QToolButton#nav:hover { background: %(hover)s; }
QToolButton#nav:focus { border: 2px solid %(accent)s; padding: 6px 9px 6px 7px; }
QToolButton#nav[compactNav="true"] { padding: 5px 4px; }
QToolButton#nav[compactNav="true"]:focus { padding: 4px 3px; }
QToolButton#nav::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right;
    width: 10px; height: 10px; }
QToolButton#nav[compactNav="true"]::menu-indicator { width: 0px; height: 0px; image: none; }
QPushButton#nav[compactNav="true"], QPushButton#nav[compactNav="true"]:checked,
QPushButton#nav[compactNav="true"]:disabled { text-align: center; padding: 5px 4px; }
QPushButton#nav[compactNav="true"]:focus,
QPushButton#nav[compactNav="true"]:checked:focus { padding: 4px 3px; }
QPushButton#nav[compactCount="true"], QPushButton#nav[compactCount="true"]:disabled,
QPushButton#nav[compactCount="true"]:checked { padding: 4px 4px 22px; min-height: 26px; text-align: center; }
QPushButton#nav[compactCount="true"]:focus,
QPushButton#nav[compactCount="true"]:checked:focus { padding: 3px 3px 21px; }
QLabel#navigationCountBadge { background: %(hover)s; color: %(accent)s;
    border: none; border-radius: 8px; padding: 0px 5px; min-width: 18px; min-height: 16px;
    font-size: 11px; font-weight: 600; }
QLabel#navigationCountBadge:disabled { background: %(disabled_bg)s; color: %(disabled)s; }
QPushButton#filter:hover, QPushButton#nav:hover { background: %(hover)s; }
QPushButton#filter:checked, QPushButton#nav:checked { background: %(selection)s; color: %(selection_text)s; font-weight: 600; }
QPushButton#filter:focus, QPushButton#nav:focus { border: 2px solid %(accent)s; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit, QTimeEdit {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s; border-radius: 6px;
    border-bottom-color: %(control_border)s;
    padding: 6px 9px; min-height: 18px; selection-background-color: %(selection)s; selection-color: %(selection_text)s;
}
QLineEdit, QTextEdit, QPlainTextEdit { placeholder-text-color: %(placeholder)s; }
QLineEdit:read-only, QTextEdit:read-only, QPlainTextEdit:read-only { background: %(subtle_surface)s; }
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover { border-color: %(control_border)s; }
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled { placeholder-text-color: %(disabled)s; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QAbstractSpinBox:focus {
    border-color: %(accent)s;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s;
}
QComboBox { padding-right: 28px; }
QComboBox::drop-down { width: 24px; border: none; background: transparent; }
QAbstractSpinBox { padding-right: 28px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { border: none; background: transparent; width: 24px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background: %(hover)s; }
QDateTimeEdit[calendarPopup="true"] { padding: 4px 34px 4px 9px; min-height: 22px; }
QDateTimeEdit[calendarPopup="true"]:on { border-color: %(accent)s; }
QDateTimeEdit[calendarPopup="true"]::drop-down {
    subcontrol-origin: padding; subcontrol-position: right center;
    width: 30px; background: transparent; border: none; border-radius: 5px;
}
QDateTimeEdit[calendarPopup="true"]::drop-down:hover:enabled { background: %(hover)s; }
QDateTimeEdit[calendarPopup="true"]::drop-down:on { background: %(notice_bg)s; }
QDateTimeEdit[calendarPopup="true"]::down-arrow, QDateTimeEdit[calendarPopup="true"]::down-arrow:on,
QDateTimeEdit[calendarPopup="true"]::down-arrow:hover, QDateTimeEdit[calendarPopup="true"]::down-arrow:focus {
    image: url(%(calendar_icon)s); width: 14px; height: 14px;
}
QComboBox QAbstractItemView { color: %(ink)s; background: %(surface)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; border: 1px solid %(border)s; }
QAbstractItemView, QListView, QListWidget, QTreeView, QTreeWidget, QTableView, QTableWidget {
    color: %(ink)s; background: %(surface)s; alternate-background-color: %(raised)s; border: 1px solid %(border)s;
    border-radius: 8px; gridline-color: %(border)s; selection-background-color: %(selection)s; selection-color: %(selection_text)s; outline: 0;
}
QListWidget#projectList, QTableWidget#documentTable { border: none; border-radius: 0; }
QAbstractItemView::item { padding: 8px 10px; border: 1px solid transparent; }
QAbstractItemView::item:hover { background: %(hover)s; color: %(ink)s; border: 1px solid transparent; }
QAbstractItemView::item:selected, QAbstractItemView::item:selected:hover,
QListView::item:selected:active, QListView::item:selected:!active,
QTreeView::item:selected:active, QTreeView::item:selected:!active,
QTableView::item:selected:active, QTableView::item:selected:!active {
    background: %(selection)s; color: %(selection_text)s; border: 1px solid transparent;
}
QListView::item:!selected:hover, QTreeView::item:!selected:hover,
QTableView::item:!selected:hover { background: %(hover)s; color: %(ink)s; }
QAbstractItemView::item:focus, QAbstractItemView::item:selected:focus,
QAbstractItemView::item:hover:focus, QAbstractItemView::item:selected:hover:focus { border: 1px solid %(accent)s; }
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
QMenu { background: %(surface)s; border-color: %(button_border)s; border-radius: 8px; padding: 4px; }
QMenu::item { background: %(surface)s; padding: 6px 28px 6px 10px; margin: 1px; border: none; border-radius: 4px; }
QMenu::item:selected, QMenuBar::item:selected { color: %(ink)s; background: %(row_selected)s; }
QMenu::item:pressed { color: %(ink)s; background: %(raised)s; }
QMenu::item:disabled { color: %(disabled)s; background: %(surface)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 3px 7px; }
QToolTip { color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s; padding: 5px 8px; }
QCheckBox, QRadioButton { spacing: 7px; }
QCheckBox:disabled, QRadioButton:disabled { color: %(disabled)s; }
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border: 2px solid %(accent)s; }
QGroupBox { color: %(ink)s; border: 1px solid %(border)s; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
/* Calendar popups use the native model, headers, editor and keyboard handling. */
QCalendarWidget { min-width: 280px; min-height: 248px; background: %(surface)s;
    border: 1px solid %(button_border)s; border-radius: 7px; }
QCalendarWidget QWidget#qt_calendar_navigationbar {
    color: %(ink)s; background: %(subtle_surface)s; border-bottom: 1px solid %(border)s;
}
QCalendarWidget QToolButton {
    color: %(ink)s; background: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 3px 6px; min-width: 20px; min-height: 24px;
}
QCalendarWidget QToolButton:focus { border-color: %(accent)s; }
QCalendarWidget QToolButton:hover:enabled { background: %(hover)s; }
QCalendarWidget QToolButton:pressed:enabled { background: %(raised)s; }
QCalendarWidget QToolButton:disabled { color: %(disabled)s; }
QCalendarWidget QToolButton#qt_calendar_prevmonth {
    qproperty-icon: url(%(left_icon)s); qproperty-iconSize: 14px 14px;
}
QCalendarWidget QToolButton#qt_calendar_nextmonth {
    qproperty-icon: url(%(right_icon)s); qproperty-iconSize: 14px 14px;
}
QCalendarWidget QToolButton#qt_calendar_monthbutton { padding-right: 20px; }
QCalendarWidget QToolButton#qt_calendar_monthbutton::menu-indicator {
    subcontrol-origin: padding; subcontrol-position: right center;
    image: url(%(chevron_icon)s); width: 9px; height: 9px; right: 5px;
}
QCalendarWidget QAbstractItemView, QCalendarWidget QAbstractItemView:focus {
    color: %(ink)s; background: %(surface)s; padding: 0; border: none; border-radius: 0;
    selection-background-color: %(selection)s; selection-color: %(selection_text)s;
}
QCalendarWidget QAbstractItemView::item { padding: 0; }
QCalendarWidget QSpinBox, QCalendarWidget QSpinBox:focus {
    padding: 2px 24px 2px 4px; min-width: 64px; min-height: 24px; border-radius: 5px;
}
QCalendarWidget QSpinBox QLineEdit, QCalendarWidget QSpinBox QLineEdit:focus {
    padding: 0; border: none; min-height: 0;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 16px; background: transparent; margin: 4px 0; border: none; }
QScrollBar:horizontal { height: 16px; background: transparent; margin: 0 4px; border: none; }
QScrollBar::handle:vertical { background: %(scroll_handle)s; border-radius: 3px; min-height: 32px; margin: 0 5px; }
QScrollBar::handle:horizontal { background: %(scroll_handle)s; border-radius: 3px; min-width: 32px; margin: 5px 0; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover,
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed { background: %(scroll_hover)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; background: transparent; border: none; image: none; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; background: transparent; border: none; image: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: %(hover)s; }
QProgressBar { background: %(raised)s; color: %(ink)s; border: none; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; margin: 0; }
QSlider::groove:horizontal { background: %(border)s; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: %(accent)s; border: 1px solid %(accent)s; border-radius: 7px; width: 14px; margin: -5px 0; }
FeedbackBubble { background: %(surface)s; color: %(ink)s; border: 1px solid %(border)s; border-radius: 8px; }
FeedbackBubble QLabel { background: transparent; color: %(ink)s; }
QWidget#manager[dense="true"] QPushButton, QWidget#manager[dense="true"] QToolButton {
    padding: 4px 8px; min-height: 18px; font-size: 14px;
}
QWidget#manager[dense="true"] QPushButton:focus, QWidget#manager[dense="true"] QToolButton:focus {
    padding: 3px 7px;
}
QWidget#manager[dense="true"] QToolButton#nav { padding: 4px 8px 4px 6px; }
QWidget#manager[dense="true"] QToolButton#nav:focus { padding: 3px 7px 3px 5px; }
QWidget#manager[dense="true"] QPushButton#nav[compactCount="true"],
QWidget#manager[dense="true"] QPushButton#nav[compactCount="true"]:disabled,
QWidget#manager[dense="true"] QPushButton#nav[compactCount="true"]:checked {
    min-height: 26px; padding: 4px 4px 22px; text-align: center;
}
QWidget#manager[dense="true"] QPushButton#nav[compactCount="true"]:focus,
QWidget#manager[dense="true"] QPushButton#nav[compactCount="true"]:checked:focus {
    padding: 3px 3px 21px;
}
QWidget#manager[dense="true"] QLineEdit, QWidget#manager[dense="true"] QComboBox,
QWidget#manager[dense="true"] QDateEdit, QWidget#manager[dense="true"] QDateTimeEdit,
QWidget#manager[dense="true"] QTimeEdit, QWidget#manager[dense="true"] QSpinBox,
QWidget#manager[dense="true"] QDoubleSpinBox { padding: 4px; min-height: 18px; font-size: 14px; }
QWidget#manager[dense="true"] QComboBox, QWidget#manager[dense="true"] QDateEdit,
QWidget#manager[dense="true"] QDateTimeEdit, QWidget#manager[dense="true"] QTimeEdit,
QWidget#manager[dense="true"] QSpinBox, QWidget#manager[dense="true"] QDoubleSpinBox { padding-right: 26px; }
QWidget#manager[dense="true"] QDateTimeEdit[calendarPopup="true"] { padding: 4px 34px 4px 9px; min-height: 22px; }
QWidget#manager[dense="true"] QTabBar::tab:top { padding: 6px 10px; font-size: 13px; }
QWidget#manager[dense="true"] QLabel#title, QWidget#manager[dense="true"] QLabel#sectionTitle { font-size: 15px; }
QWidget#manager[dense="true"] QLabel#notice { padding: 5px 8px; font-size: 12px; }
""" % c
    if role in ('feedback', 'bubble'):
        rules += ('\nQWidget#feedbackBubble { background: %(surface)s; color: %(ink)s; '
                  'border: 1px solid %(border)s; border-radius: 8px; }\n') % c
    # Shared presentation primitives; no model refresh or business-setting writes.
    rules += """
QFrame#topWorkspaceBar { background: %(nav_bg)s; border: none; border-radius: 0; border-bottom: 1px solid %(border)s; }
QFrame#topWorkspaceBar QLabel#brandName { color: %(nav_ink)s; font-size: 15px; font-weight: 600; }
QWidget#workbenchCanvas { background: %(surface)s; }
QFrame#workbenchHeader, QWidget#deliveryCommandBar {
    background: %(subtle_surface)s; border: none; border-radius: 0; border-bottom: 1px solid %(border)s;
}
QFrame#workbenchHeader QLabel#pageTitle { font-size: 17px; }
QFrame#workbenchHeader QLineEdit { padding: 4px 10px; min-height: 22px; }
QDialog#deliveryWorkspace { background: %(surface)s; border: none; border-radius: 0; }
QDialog#deliveryWorkspace QLabel#deliveryTypeTotal { color: %(ink)s; font-size: 13px; font-weight: 600; padding: 0; }
QDialog#deliveryWorkspace QTextBrowser#deliveryTypeCounts { color: %(muted)s; font-size: 12px; }
QLabel#deliverySectionTitle { color: %(ink)s; font-size: 14px; font-weight: 600; padding: 0; }
QWidget#deliveryViewBar { background: transparent; border: none; border-radius: 0; border-bottom: 1px solid %(border)s; }
QTabBar#deliveryViewTabs::tab:top, QTabBar#deliveryViewTabs::tab:top:!selected {
    color: %(muted)s; background: transparent; border: 1px solid transparent; border-radius: 0;
    padding: 4px 12px; min-height: 20px; font-size: 13px;
}
QTabBar#deliveryViewTabs::tab:top:selected {
    color: %(accent)s; background: %(row_selected)s; border: 1px solid transparent;
    border-bottom-color: %(accent)s; padding: 4px 12px; font-size: 13px; font-weight: 600;
}
QTabBar#deliveryViewTabs::tab:top:!selected:hover { color: %(ink)s; background: %(hover)s; }
QTabBar#deliveryViewTabs::tab:top:focus { border-color: %(accent)s; }
QTabBar#deliveryViewTabs::tab:top:disabled { color: %(disabled)s; }
QTableWidget#deliveryProjects, QTableWidget#deliveryRecords { border: none; border-radius: 0; }
QSplitter#deliveryPanels::handle { background: transparent; border: none; border-top: 1px solid %(border)s; }
QSplitter#deliveryPanels::handle:hover { border-top-color: %(control_border)s; }
QWidget#documentSurface QLabel#selectedProjectName, QWidget#documentSurface QLabel#projectMetadata,
QWidget#documentSurface QLabel#sectionTitle, QWidget#projectSurface QLabel#queueHeader { padding: 0; }
QLabel#selectedProjectName { font-size: 18px; font-weight: 600; }
QLabel#queueHeader { color: %(ink)s; font-size: 15px; font-weight: 600; }
QLabel#projectMetadata { color: %(muted)s; font-size: 12px; }
QWidget#projectDetailActions { background: transparent; border: none; }
QFrame#sidebar { background: %(nav_bg)s; border: none; border-radius: 0px; }
QFrame#sidebar QLabel#brandName { color: %(nav_ink)s; }
QFrame#sidebar QLabel#subtle, QFrame#sidebar QLabel#workspaceStatus { color: %(nav_muted)s; }
QLabel#projectStatusBadge { color: %(state_new)s; background: %(raised)s;
    border: none; border-radius: 5px; padding: 3px 7px; font-size: 12px; font-weight: 500; }
QLabel#projectStatusBadge[state="开发中"] { color: %(state_working)s; background: %(info_bg)s; }
QLabel#projectStatusBadge[state="待交付"] { color: %(state_pending)s; background: %(warning_bg)s; }
QLabel#projectStatusBadge[state="已交付"] { color: %(state_delivered)s; background: %(success_bg)s; }
QWidget#dialogHeader { background: transparent; border: none; }
QLabel#headerSubtitle { color: %(muted)s; font-size: 13px; }
QLabel#headerContext { color: %(muted)s; font-size: 11px; }
QFrame#metricStrip { background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; }
QFrame#metricDivider { background: %(border)s; border: none; }
QLabel#metricValue { color: %(ink)s; font-size: 22px; font-weight: 600; }
QLabel#metricCaption { color: %(muted)s; font-size: 11px; }
QLabel#statusBanner { color: %(ink)s; background: %(subtle_surface)s; border: 1px solid %(border)s;
    border-radius: 8px; padding: 8px 12px; font-size: 12px; }
QLabel#statusBanner[tone="busy"] { background: %(info_bg)s; color: %(state_working)s; }
QLabel#statusBanner[tone="success"] { background: %(success_bg)s; color: %(state_delivered)s; }
QLabel#statusBanner[tone="warning"] { background: %(warning_bg)s; color: %(state_pending)s; }
QLabel#statusBanner[tone="error"] { background: %(error_bg)s; color: %(error)s; }
QFrame#actionBar { background: %(subtle_surface)s; border: none; border-top: 1px solid %(border)s; }
QLabel#workspaceStatus { color: %(muted)s; font-size: 11px; padding: 8px 2px; }
QLabel#saveState { color: %(muted)s; font-size: 11px; }
QPushButton#subtle { background: transparent; border-color: transparent; }
QPushButton#subtle:hover { background: %(hover)s; border-color: %(border)s; }
QPushButton#danger { color: %(error)s; background: transparent; border-color: %(border)s; }
QPushButton#danger:hover { background: %(error_bg)s; }
QPushButton#danger:disabled { color: %(disabled)s; background: %(disabled_bg)s; }
QWidget#projectSurface { background: %(subtle_surface)s; border: none; border-radius: 0; }
QFrame#projectQueueHeader, QWidget#projectQueueTools { background: transparent; border: none; }
QWidget#documentSurface { background: %(surface)s; border: none; border-radius: 0; }
QListWidget#projectList { background: transparent; border: none; }
QSplitter#projectWorkspaceSplit::handle { background: transparent; border: none; border-left: 1px solid %(border)s; }
QSplitter#projectWorkspaceSplit::handle:hover { border-left-color: %(control_border)s; }
QFrame#workbenchFooter { background: %(nav_bg)s; border: none; border-radius: 0; border-top: 1px solid %(border)s; }
QAbstractSpinBox QLineEdit { border: none; padding: 0px; min-height: 0px; background: transparent; }
QPlainTextEdit#releaseNotes { background: %(surface)s; border-color: %(border)s; padding: 12px; min-height: 96px; }
QTableWidget#materialRules, QTableWidget#materialResults, QTableWidget#materialChoices {
    background: %(surface)s; alternate-background-color: %(subtle_surface)s; border: 1px solid %(border)s;
    border-radius: 8px; gridline-color: transparent; }
QWidget#manager[dense="true"] QFrame#workbenchHeader QLabel#pageTitle { font-size: 16px; }
""" % c
    base = _base_stylesheet(actual) if QApplication.instance() is not None else ''
    from studio_design import chrome
    return base + '\n/* WorkBox semantic appearance */\n' + rules + chrome(c)


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
        QPalette.Dark: 'border', QPalette.Shadow: 'border', QPalette.PlaceholderText: 'placeholder',
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
        app_font = QFont()
        app_font.setFamilies(ui_font_families())
        app_font.setPointSize(10)
        app_font.setStyleHint(QFont.SansSerif)
        app_font.setStyleStrategy(QFont.PreferAntialias)
        app.setFont(app_font)
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
        if event.type() == QEvent.Show and isinstance(watched, QCalendarWidget):
            self._apply_calendar(watched)
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

    def _apply_calendar(self, calendar):
        colors = tokens(self.resolved)
        header = calendar.headerTextFormat()
        header.setForeground(QColor(colors['muted']))
        header.setBackground(QColor(colors['subtle_surface']))
        calendar.setHeaderTextFormat(header)
        for day in (Qt.Saturday, Qt.Sunday):
            weekend = calendar.weekdayTextFormat(day)
            weekend.setForeground(QColor(colors['muted']))
            calendar.setWeekdayTextFormat(day, weekend)

    def _apply(self):
        # 设置应用级样式与 palette，使无父级菜单、系统提示也得到同一主题。
        self.app.setStyleSheet(stylesheet(self.resolved))
        self.app.setPalette(_palette(self.resolved))
        from ui_components import init_fluent_theme
        init_fluent_theme(self.resolved, tokens(self.resolved)['accent'])
        # Qt owns the popup, and a palette event can arrive before its model exists.
        for widget in self.app.allWidgets():
            if isinstance(widget, QCalendarWidget) and widget.isVisible():
                self._apply_calendar(widget)
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
        # Fluent组件拥有自己的样式与委托，遍历子控件强制unpolish会重入布局。
        # 应用级主题变化由Qt与Fluent样式管理器共同传播。
        widget.updateGeometry()
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
