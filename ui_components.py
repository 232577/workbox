"""基础 Fluent 控件适配；业务模型、原生窗口和滚动仍由原有 Qt 层负责。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget, QFrame, QHBoxLayout, QSizePolicy
from qfluentwidgets.common.icon import FluentIconBase, FluentIconEngine, toQIcon
from control_styles import control_style
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
    qconfig,
    SegmentedWidget,
)

__all__ = ['button', 'LineEdit', 'CheckBox', 'SpinBox', 'ComboBox', 'init_fluent_theme',
           'StatusBanner', 'DialogHeader', 'MetricStrip']


class ElidedLabel(QLabel):
    """One-line context retains complete plain text in its tooltip and a11y name."""
    def __init__(self, text='', parent=None):
        super().__init__(parent)
        self.fullText = ''
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self.fullText = str(text)
        self.setToolTip(self.fullText)
        self.setAccessibleName(self.fullText)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self.fullText, Qt.ElideRight, max(1, self.width())))

    def resizeEvent(self, event):
        self._elide()
        super().resizeEvent(event)


class StatusBanner(QLabel):
    """Explicit text + tone; never infer success from a color or message."""
    TONES = {'neutral': '提示', 'busy': '处理中', 'success': '已完成', 'warning': '需留意', 'error': '未完成'}

    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setObjectName('statusBanner')
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setTone('neutral')

    def setTone(self, tone):
        if tone not in self.TONES:
            raise ValueError('Unknown feedback tone')
        self.setProperty('tone', tone)
        self.setAccessibleDescription(self.TONES[tone])
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        self.update()


class DialogHeader(QWidget):
    """Shared title/context, preserving the native dialog and focus loop."""
    def __init__(self, title, subtitle='', parent=None, *, icon=None, eyebrow='WORKBOX'):
        super().__init__(parent)
        self.setObjectName('dialogHeader')
        self._source_icon = icon
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if icon is not None:
            self.icon_label = QLabel(self)
            self.icon_label.setFixedSize(24, 28)
            self.icon_label.setAlignment(Qt.AlignCenter)
            self._refresh_icon()
            qconfig.themeChanged.connect(self._refresh_icon)
            row.addWidget(self.icon_label, 0, Qt.AlignTop)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        self.title = QLabel(title)
        self.title.setObjectName('pageTitle')
        self.title.setTextFormat(Qt.PlainText)
        self.subtitle = ElidedLabel(subtitle)
        self.subtitle.setObjectName('headerSubtitle')
        self.eyebrow = QLabel(eyebrow)
        self.eyebrow.setObjectName('headerContext')
        self.eyebrow.setTextFormat(Qt.PlainText)
        self.eyebrow.setVisible(bool(eyebrow and eyebrow != 'WORKBOX'))
        for label in (self.title, self.subtitle, self.eyebrow):
            copy.addWidget(label)
        self.subtitle.setVisible(bool(subtitle))
        row.addLayout(copy, 1)

    def setSubtitle(self, text):
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def _refresh_icon(self, *_):
        icon = self._source_icon
        if isinstance(icon, FluentIconBase):
            from theme import tokens
            icon = icon.icon(color=QColor(tokens()['muted']))
        self.icon_label.setPixmap(toQIcon(icon).pixmap(QSize(20, 20)))


class MetricStrip(QFrame):
    """Small stable counters. Updating values never rebuilds widgets or queries."""
    def __init__(self, captions, parent=None):
        super().__init__(parent)
        self.setObjectName('metricStrip')
        self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        row=QHBoxLayout(self);row.setContentsMargins(16,8,16,8);row.setSpacing(16)
        self.captions=tuple(captions);self.value_labels=[]
        for index, caption in enumerate(self.captions):
            if index:
                divider = QFrame(self)
                divider.setObjectName('metricDivider')
                divider.setFixedWidth(1)
                row.addWidget(divider)
            column=QVBoxLayout();column.setSpacing(0)
            value=QLabel('—');value.setObjectName('metricValue');value.setTextFormat(Qt.PlainText)
            value.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            title=QLabel(caption);title.setObjectName('metricCaption');title.setTextFormat(Qt.PlainText)
            column.addWidget(value);column.addWidget(title);row.addLayout(column,1)
            self.value_labels.append(value)

    def setValues(self, values):
        values=tuple(values)
        if len(values)!=len(self.value_labels):raise ValueError('Metric count mismatch')
        for caption,label,value in zip(self.captions,self.value_labels,values):
            label.setText(str(value));label.setAccessibleName(f'{caption}：{value}')


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

        self.setFixedHeight(32)

        # A widget-local Fluent sheet wins over the application padding during
        # Qt polish and otherwise reduces this to 22px despite minimumHeight.
        from theme import tokens
        self.setCustomFocusedBorderColor(tokens('light')['accent'], tokens('dark')['accent'])
        setCustomStyleSheet(self, control_style('light', 'line'), control_style('dark', 'line'))

    def contextMenuEvent(self, event):
        _exec_native_edit_menu(self, event.globalPos())
        event.accept()


class SpinBox(_FluentSpinBox):
    """保留 QSpinBox 范围、校验、步长和信号，编辑菜单沿用 Qt。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from theme import tokens
        self.setCustomFocusedBorderColor(tokens('light')['accent'], tokens('dark')['accent'])
        setCustomStyleSheet(self, control_style('light', 'spin'), control_style('dark', 'spin'))
        for control, name in ((self.upButton, '增加数值'), (self.downButton, '减少数值')):
            control.setAccessibleName(name)
            control.setToolTip(name)
            setCustomStyleSheet(control, control_style('light', 'spin-button'), control_style('dark', 'spin-button'))
        self.valueChanged.connect(self._update_step_actions)
        self._update_step_actions()

    def _update_step_actions(self):
        # Fluent renders the inline buttons separately from Qt's native stepper.
        steps = self.stepEnabled()
        self.upButton.setEnabled(bool(steps & self.StepEnabledFlag.StepUpEnabled))
        self.downButton.setEnabled(bool(steps & self.StepEnabledFlag.StepDownEnabled))

    def setValue(self, value):
        super().setValue(value)
        self._update_step_actions()

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self._update_step_actions()

    def setMinimum(self, minimum):
        super().setMinimum(minimum)
        self._update_step_actions()

    def setMaximum(self, maximum):
        super().setMaximum(maximum)
        self._update_step_actions()

    def setWrapping(self, enabled):
        super().setWrapping(enabled)
        self._update_step_actions()

    def setReadOnly(self, read_only):
        super().setReadOnly(read_only)
        self._update_step_actions()

    def _showContextMenu(self, position):
        _exec_native_edit_menu(self.lineEdit(), self.mapToGlobal(position))


class CheckBox(_FluentCheckBox):
    """复选标记由 Fluent 绘制，避免叠加应用基础皮肤的指示器图片。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        def chrome(mode):
            from theme import tokens
            return ('CheckBox { color: %(ink)s; spacing: 8px; font-size: 14px; '
                    'border: 1px solid transparent; border-radius: 4px; padding: 0 2px; }'
                    'CheckBox:focus { border-color: %(accent)s; }'
                    'CheckBox:disabled { color: %(disabled)s; }'
                    'CheckBox::indicator, CheckBox::indicator:checked, '
                    'CheckBox::indicator:unchecked, CheckBox::indicator:indeterminate '
                    '{ image: none; border: none; background: transparent; width: 17px; height: 17px; }'
                    ) % tokens(mode)
        setCustomStyleSheet(self, chrome('light'), chrome('dark'))

    def _checkedFill(self, colors):
        if not self.isEnabled():
            return colors['disabled_check']
        if self.isDown():
            return colors['accent_pressed']
        return colors['accent_hover'] if self.isHover else colors['accent']

    def _borderColor(self):
        from theme import tokens
        colors = tokens()
        if self.isChecked():
            return QColor(self._checkedFill(colors))
        if not self.isEnabled():
            return QColor(colors['button_border'])
        return QColor(colors['accent' if self.isHover else 'control_border'])

    def _backgroundColor(self):
        from theme import tokens
        colors = tokens()
        if self.isChecked():
            return QColor(self._checkedFill(colors))
        if not self.isEnabled():
            return QColor(colors['disabled_bg'])
        if self.isDown():
            return QColor(colors['raised'])
        return QColor(colors['hover' if self.isHover else 'surface'])


class ComboBox(QComboBox):
    """使用 Qt 原生数据模型、弹出列表与信号，仅复用 Fluent 外观资源。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        FluentStyleSheet.COMBO_BOX.apply(self)
        def native_chrome(theme):
            arrow = FluentIcon.ARROW_DOWN.path(theme)
            return (control_style('dark' if theme == Theme.DARK else 'light', 'combo') +
                    "ComboBox::down-arrow, ComboBox::down-arrow:on, "
                    "ComboBox::down-arrow:hover, ComboBox::down-arrow:focus "
                    f"{{ image: url({arrow}); width: 10px; height: 10px; }}")
        setCustomStyleSheet(self, native_chrome(Theme.LIGHT), native_chrome(Theme.DARK))


class StatusSegments(SegmentedWidget):
    """Keep Fluent navigation/keyboard behavior with WorkBox's selection surface."""

    def paintEvent(self, event):
        QWidget.paintEvent(self, event)
        item = self.currentItem()
        if item is None:
            return
        from theme import tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(tokens()['notice_bg']))
        rect = item.rect().adjusted(1, 1, -1, -1).translated(int(self.slideAni.value()), 0)
        painter.drawRoundedRect(rect, 6, 6)


class NavigationButton(QPushButton):
    """交由 Qt 统一布局图标、文字和原生菜单指示器。"""

    def __init__(self, text: str):
        super().__init__(text)
        FluentStyleSheet.BUTTON.apply(self)
        self._source_icon = None
        self._compact_count_height = None
        self._count_label = QLabel(self)
        self._count_label.setObjectName('navigationCountBadge')
        self._count_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._count_label.setFocusPolicy(Qt.NoFocus)
        self._count_label.setAlignment(Qt.AlignCenter)
        self._count_label.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 3)
        layout.setSpacing(0)
        layout.addStretch()
        layout.addWidget(self._count_label, 0, Qt.AlignHCenter)
        self.setCheckable(True)
        self.toggled.connect(self._refresh_icon)
        qconfig.themeChanged.connect(self._refresh_icon)

    def setCompactCount(self, text: str | None):
        enabled = text is not None
        if enabled and self._compact_count_height is None:
            self._compact_count_height = self.minimumHeight()
            self.setMinimumHeight(max(54, self._compact_count_height))
        elif not enabled and self._compact_count_height is not None:
            self.setMinimumHeight(self._compact_count_height)
            self._compact_count_height = None
        self.setProperty('compactCount', enabled)
        self._count_label.setText(text or '')
        self._count_label.setVisible(enabled)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        self.update()

    def setIcon(self, icon):
        self._source_icon = icon
        self._refresh_icon()

    def _refresh_icon(self, *_):
        if isinstance(self._source_icon, FluentIconBase):
            from theme import tokens
            if self.isChecked():
                color = tokens()['nav_accent'] if self.property('softSelection') else 'white'
                icon = self._source_icon.icon(color=QColor(color))
            else:
                icon = self._source_icon.icon(color=QColor(tokens()['nav_ink']))
            super().setIcon(icon)
        else:
            super().setIcon(toQIcon(self._source_icon) if self._source_icon is not None else QIcon())


class NavigationMenuButton(QToolButton):
    """菜单导航使用 Qt 工具按钮，避免 QPushButton 的菜单内容二次缩进。"""

    def __init__(self, text: str):
        super().__init__()
        FluentStyleSheet.BUTTON.apply(self)
        self.setText(text)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        # Qt 的 InstantPopup 虽声明 ShowMenu 无障碍动作，却不执行它。
        # 保留标准 DelayedPopup 的可访问接口，普通激活也立即展开菜单。
        self.setPopupMode(QToolButton.DelayedPopup)
        self.clicked.connect(self.showMenu)
        self.setFocusPolicy(Qt.StrongFocus)
        self._source_icon = None
        qconfig.themeChanged.connect(self._refresh_icon)

    def setText(self, text):
        super().setText(text)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text else Qt.ToolButtonIconOnly)

    def setIcon(self, icon):
        self._source_icon = icon
        self._refresh_icon()

    def _refresh_icon(self, *_):
        if isinstance(self._source_icon, FluentIconBase):
            from theme import tokens
            super().setIcon(self._source_icon.icon(color=QColor(tokens()['nav_ink'])))
        else:
            super().setIcon(toQIcon(self._source_icon) if self._source_icon is not None else QIcon())


class _NativeButtonIcon:
    """Let Qt measure and draw the icon and caption together in every state."""

    def setIcon(self, icon):
        if isinstance(icon, FluentIconBase):
            icon = QIcon(FluentIconEngine(icon, reverse=isinstance(self, PrimaryPushButton)))
        QPushButton.setIcon(self, toQIcon(icon) if icon is not None else QIcon())

    def icon(self):
        return QPushButton.icon(self)

    def paintEvent(self, event):
        QPushButton.paintEvent(self, event)


class _WorkBoxPushButton(_NativeButtonIcon, PushButton):
    pass


class _WorkBoxPrimaryButton(_NativeButtonIcon, PrimaryPushButton):
    pass


class _WorkBoxToggleButton(_NativeButtonIcon, ToggleButton):
    pass


def button(text: str, primary: bool | str = False):
    """兼容紧凑窗口布尔主按钮及旧界面的 primary/filter/nav 角色。"""
    role = primary if isinstance(primary, str) else ('primary' if primary else '')
    kind = {'primary': _WorkBoxPrimaryButton, 'filter': _WorkBoxToggleButton,
            'nav': NavigationButton, 'nav-menu': NavigationMenuButton}.get(role, _WorkBoxPushButton)
    widget = kind(text)
    widget.setCursor(Qt.PointingHandCursor)
    # Keep Qt's text-derived accessible name live when captions change.
    # Callers may still supply an explicit, stable descriptive name.
    if role:
        widget.setObjectName('nav' if role == 'nav-menu' else role)
    def native_menu_arrow(theme):
        arrow = FluentIcon.ARROW_DOWN.path(theme)
        rules = (control_style('dark' if theme == Theme.DARK else 'light', 'button') +
                "PushButton::menu-indicator { subcontrol-origin: padding; "
                "subcontrol-position: right center; width: 10px; height: 10px; "
                f"image: url({arrow}); }}"
                "QPushButton#nav::menu-indicator { subcontrol-origin: padding; "
                "subcontrol-position: center right; width: 10px; height: 10px; top: 1px; "
                f"image: url({arrow}); }}")
        if role == 'danger':
            # Fluent's widget-local sheet overrides application selectors.
            # Keep semantic roles local too, using the same central tokens.
            from theme import tokens
            colors = tokens('dark' if theme == Theme.DARK else 'light')
            rules += ("PushButton#danger { color: %(error)s; background: transparent; "
                      "border-color: %(button_border)s; }"
                      "PushButton#danger:hover, PushButton#danger:pressed { background: %(error_bg)s; }"
                      "PushButton#danger:focus { border-color: %(error)s; }"
                      "PushButton#danger:disabled { color: %(disabled)s; background: %(disabled_bg)s; "
                      "border-color: transparent; }") % colors
        if role == 'nav-menu':
            nav_arrow = FluentIcon.ARROW_DOWN.path(theme)
            rules += ('QToolButton#nav::menu-indicator { subcontrol-origin: padding; '
                      'subcontrol-position: center right; width: 10px; height: 10px; '
                      f'image: url({nav_arrow}); }}'
                      'QToolButton#nav[compactNav="true"]::menu-indicator { image: none; width: 0px; height: 0px; }')
        return rules
    setCustomStyleSheet(widget, native_menu_arrow(Theme.LIGHT), native_menu_arrow(Theme.DARK))
    return widget


def init_fluent_theme(mode: str, accent: str) -> None:
    """外观由 WorkBox 主题入口传入；不读写组件库配置文件。"""
    themes = {'light': Theme.LIGHT, 'dark': Theme.DARK, 'system': Theme.AUTO}
    if mode not in themes:
        raise ValueError(f'不支持的外观模式：{mode}')
    from theme import ui_font_families
    setFontFamilies(ui_font_families(), save=False)
    setTheme(themes[mode], save=False)
    setThemeColor(accent, save=False)
