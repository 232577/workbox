"""基础 Fluent 控件适配；业务模型、原生窗口和滚动仍由原有 Qt 层负责。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QWidget
from qfluentwidgets import (
    CheckBox as _FluentCheckBox,
    FluentIcon,
    FluentStyleSheet,
    LineEdit as _FluentLineEdit,
    PrimaryPushButton,
    PushButton,
    ToggleButton,
    SpinBox as _FluentSpinBox,
    Theme,
    setFontFamilies,
    setCustomStyleSheet,
    setTheme,
    setThemeColor,
)

__all__ = ['button', 'LineEdit', 'CheckBox', 'SpinBox', 'ComboBox', 'init_fluent_theme']


def _exec_native_edit_menu(edit: QLineEdit, global_position) -> None:
    """保留 Qt 的剪切、复制、粘贴及快捷键，不启用 Fluent 浮层菜单。"""
    menu = QLineEdit.createStandardContextMenu(edit)
    try:
        menu.exec(global_position)
    finally:
        menu.deleteLater()


class LineEdit(_FluentLineEdit):
    """保留 QLineEdit 类型与编辑契约，右键使用原生 QMenu。"""

    def __init__(self, text: str | QWidget | None = None, parent: QWidget | None = None):
        if isinstance(text, str):
            super().__init__(parent)
            self.setText(text)
        else:
            super().__init__(parent if parent is not None else text)

    def contextMenuEvent(self, event):
        _exec_native_edit_menu(self, event.globalPos())
        event.accept()


class SpinBox(_FluentSpinBox):
    """保留 QSpinBox 范围、校验、步长和信号，编辑菜单沿用 Qt。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 应用的通用输入框和工具按钮内边距会破坏 Fluent 的固定尺寸。
        spin_rule = ('SpinBox, SpinBox:focus { min-height: 31px; '
                     'padding: 0px 80px 0px 10px; } '
                     'SpinBox:read-only, SpinBox[symbolVisible=false] '
                     '{ padding: 0px 10px; }')
        button_rule = ('SpinButton, SpinButton:focus { padding: 0px; '
                       'min-height: 21px; min-width: 29px; }')
        setCustomStyleSheet(self, spin_rule, spin_rule)
        for control in (self.upButton, self.downButton):
            setCustomStyleSheet(control, button_rule, button_rule)

    def _showContextMenu(self, position):
        _exec_native_edit_menu(self.lineEdit(), self.mapToGlobal(position))


class CheckBox(_FluentCheckBox):
    """复选标记由 Fluent 绘制，避免叠加应用基础皮肤的指示器图片。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rule = ('CheckBox::indicator, CheckBox::indicator:checked, '
                'CheckBox::indicator:unchecked, CheckBox::indicator:indeterminate '
                '{ image: none; border: none; background: transparent; }')
        setCustomStyleSheet(self, rule, rule)


class ComboBox(QComboBox):
    """使用 Qt 原生数据模型、弹出列表与信号，仅复用 Fluent 外观资源。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        FluentStyleSheet.COMBO_BOX.apply(self)
        def native_chrome(theme):
            arrow = FluentIcon.ARROW_DOWN.path(theme)
            return ("ComboBox::drop-down { subcontrol-origin: padding; "
                    "subcontrol-position: top right; width: 28px; border: none; background: transparent; }"
                    f"ComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 10px; }}")
        setCustomStyleSheet(self, native_chrome(Theme.LIGHT), native_chrome(Theme.DARK))


def button(text: str, primary: bool | str = False) -> PushButton:
    """兼容紧凑窗口布尔主按钮及旧界面的 primary/filter/nav 角色。"""
    role = primary if isinstance(primary, str) else ('primary' if primary else '')
    kind = PrimaryPushButton if role == 'primary' else ToggleButton if role in ('filter', 'nav') else PushButton
    widget = kind(text)
    widget.setCursor(Qt.PointingHandCursor)
    if role:
        widget.setObjectName(role)
    def native_menu_arrow(theme):
        arrow = FluentIcon.ARROW_DOWN.path(theme)
        navigation = ''
        if role == 'nav':
            # ToggleButton 的上游图标绘制依赖一致的文字留白；原生皮肤不能覆盖它。
            navigation = ("PushButton#nav, ToggleButton#nav { min-width: 0px; text-align: center; }"
                          "PushButton#nav[hasIcon=true], ToggleButton#nav[hasIcon=true] { padding: 5px 12px 6px 36px; }"
                          "PushButton#nav[compactNav=true], ToggleButton#nav[compactNav=true] { padding: 5px 12px 6px 12px; }")
        return navigation + ("PushButton::menu-indicator { subcontrol-origin: padding; "
                "subcontrol-position: right center; width: 10px; height: 10px; "
                f"image: url({arrow}); }}")
    setCustomStyleSheet(widget, native_menu_arrow(Theme.LIGHT), native_menu_arrow(Theme.DARK))
    return widget


def init_fluent_theme(mode: str, accent: str) -> None:
    """外观由 WorkBox 主题入口传入；不读写组件库配置文件。"""
    themes = {'light': Theme.LIGHT, 'dark': Theme.DARK, 'system': Theme.AUTO}
    if mode not in themes:
        raise ValueError(f'不支持的外观模式：{mode}')
    setFontFamilies(['Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'], save=False)
    setTheme(themes[mode], save=False)
    setThemeColor(accent, save=False)
