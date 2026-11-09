"""WorkBox control chrome, layered over native Qt/Fluent behavior."""


def control_style(mode, kind):
    from theme import tokens
    c = tokens(mode)
    if kind == 'button':
        return '''
PushButton, PrimaryPushButton, ToggleButton {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s;
    border-radius: 6px; padding: 6px 12px; min-height: 18px; font-size: 14px;
}
PushButton:hover, ToggleButton:hover { background: %(hover)s; border-color: %(control_border)s; }
PushButton:pressed, ToggleButton:pressed { background: %(raised)s; border-color: %(control_border)s; }
PushButton:focus, ToggleButton:focus { border: 2px solid %(accent)s; padding: 5px 11px; }
PushButton[workboxRole="secondary"] { background: transparent; border-color: transparent; color: %(muted)s; }
PushButton[workboxRole="secondary"]:hover { background: %(hover)s; color: %(ink)s; border-color: transparent; }
PushButton[workboxRole="secondary"]:pressed { background: %(raised)s; }
PushButton[workboxRole="secondary"]:focus { border-color: %(accent)s; color: %(ink)s; }
ToggleButton:checked { color: %(accent)s; background: %(notice_bg)s; border-color: %(button_border)s; }
ToggleButton:checked:hover { background: %(nav_selected)s; }
ToggleButton:checked:pressed { background: %(raised)s; }
ToggleButton:checked:focus { border-color: %(accent)s; }
PushButton:disabled, PrimaryPushButton:disabled, ToggleButton:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s;
}
PushButton[workboxRole="secondary"]:disabled, ToggleButton:checked:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: transparent;
}
PrimaryPushButton, PrimaryPushButton#primary {
    color: %(accent_text)s; background: %(accent)s; border-color: %(accent)s; font-weight: 600;
}
PrimaryPushButton:hover, PrimaryPushButton#primary:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
PrimaryPushButton:pressed, PrimaryPushButton#primary:pressed { background: %(accent_pressed)s; border-color: %(accent_pressed)s; }
PrimaryPushButton:focus, PrimaryPushButton#primary:focus { border: 2px solid %(ink)s; padding: 5px 11px; }
PrimaryPushButton:disabled, PrimaryPushButton#primary:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: transparent;
}
PrimaryPushButton#primary[contextSecondary="true"] {
    color: %(ink)s; background: %(surface)s; border-color: %(button_border)s; font-weight: 400;
}
PrimaryPushButton#primary[contextSecondary="true"]:hover { background: %(hover)s; }
PrimaryPushButton#primary[contextSecondary="true"]:pressed { background: %(raised)s; }
PrimaryPushButton#primary[contextSecondary="true"]:focus { border-color: %(accent)s; }
PrimaryPushButton#primary[contextSecondary="true"]:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s;
}
PushButton#filterChip {
    color: %(nav_accent)s; background: %(notice_bg)s; border-color: transparent;
    border-radius: 5px; padding: 3px 7px; min-height: 16px; font-size: 12px;
}
PushButton#filterChip:hover { background: %(nav_selected)s; }
PushButton#filterChip:pressed { background: %(raised)s; }
PushButton#filterChip:focus { border-color: %(accent)s; padding: 2px 6px; }
PushButton#filterChip:disabled { color: %(disabled)s; background: %(disabled_bg)s; }
PushButton#projectBackButton { background: transparent; border-color: transparent; color: %(muted)s; }
PushButton#projectBackButton:hover { background: %(hover)s; color: %(ink)s; }
PushButton#projectBackButton:pressed { background: %(raised)s; }
PushButton#projectBackButton:focus { border-color: %(accent)s; }
PushButton#projectBackButton:disabled { color: %(disabled)s; background: transparent; border-color: transparent; }
QPushButton#nav, QToolButton#nav {
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    color: %(nav_ink)s; padding: 8px 12px; min-height: 20px; text-align: left; font-weight: 500;
}
QToolButton#nav { padding-left: 10px; }
QPushButton#nav:hover, QToolButton#nav:hover { background: %(nav_hover)s; color: %(nav_ink)s; }
QPushButton#nav:pressed, QToolButton#nav:pressed { background: %(raised)s; }
QPushButton#nav:checked { background: %(selection)s; color: %(selection_text)s; font-weight: 600; }
QPushButton#nav[softSelection="true"]:checked {
    background: %(nav_selected)s; color: %(nav_accent)s; border-color: transparent; font-weight: 600;
}
QPushButton#nav:focus, QToolButton#nav:focus { border: 2px solid %(nav_accent)s; padding: 7px 11px; }
QToolButton#nav:focus { padding-left: 9px; }
QPushButton#nav[compactNav="true"], QToolButton#nav[compactNav="true"] { text-align: center; padding-left: 8px; padding-right: 8px; }
QPushButton#nav[compactCount="true"] { padding-bottom: 22px; }
QPushButton#nav[compactCount="true"]:focus { padding-bottom: 21px; }
QPushButton#nav:disabled, QToolButton#nav:disabled { color: %(disabled)s; }
QFrame#topWorkspaceBar QPushButton#nav, QFrame#topWorkspaceBar QToolButton#nav {
    background: transparent; border: 1px solid transparent; border-radius: 5px;
    color: %(nav_ink)s; padding: 5px 10px; min-height: 20px; text-align: center;
}
QFrame#topWorkspaceBar QToolButton#nav { padding: 3px 14px 4px 10px; }
QFrame#topWorkspaceBar QPushButton#nav:hover, QFrame#topWorkspaceBar QToolButton#nav:hover {
    background: %(nav_hover)s;
}
QFrame#topWorkspaceBar QPushButton#nav:pressed, QFrame#topWorkspaceBar QToolButton#nav:pressed {
    background: %(raised)s;
}
QFrame#topWorkspaceBar QPushButton#nav:checked,
QFrame#topWorkspaceBar QPushButton#nav[softSelection="true"]:checked {
    background: %(nav_selected)s; border-color: %(border)s; border-bottom-color: %(nav_accent)s;
    color: %(nav_accent)s; font-weight: 600;
}
QFrame#topWorkspaceBar QPushButton#nav:focus, QFrame#topWorkspaceBar QToolButton#nav:focus,
QFrame#topWorkspaceBar QPushButton#nav[softSelection="true"]:checked:focus {
    border-color: %(nav_accent)s;
}
QFrame#topWorkspaceBar QPushButton#nav:disabled, QFrame#topWorkspaceBar QToolButton#nav:disabled {
    color: %(disabled)s; background: transparent; border-color: transparent;
}
''' % c
    if kind == 'line':
        return '''
LineEdit, LineEdit:focus, LineEdit:disabled {
    min-height: 30px; padding: 0px 10px; border-radius: 6px;
    color: %(ink)s; placeholder-text-color: %(placeholder)s;
    background: %(surface)s; border: 1px solid %(button_border)s; border-bottom-color: %(control_border)s;
    selection-background-color: %(selection)s; selection-color: %(selection_text)s;
}
LineEdit:hover { background: %(subtle_surface)s; border-color: %(control_border)s; }
LineEdit:read-only { background: %(subtle_surface)s; }
LineEdit:focus { background: %(surface)s; border-color: %(accent)s; }
LineEdit:disabled { color: %(disabled)s; placeholder-text-color: %(disabled)s;
    background: %(disabled_bg)s; border-color: %(border)s; }
LineEditButton#lineEditButton { background: transparent; border: none; border-radius: 4px; padding: 0; }
LineEditButton#lineEditButton:hover { background: %(hover)s; }
LineEditButton#lineEditButton:pressed { background: %(raised)s; }
''' % c
    if kind == 'spin':
        return '''
SpinBox, SpinBox:focus, SpinBox:disabled {
    min-height: 30px; padding: 0 75px 0 10px; border-radius: 6px;
    color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s;
    border-bottom-color: %(control_border)s;
}
SpinBox:hover { background: %(subtle_surface)s; border-color: %(control_border)s; }
SpinBox:read-only, SpinBox[symbolVisible="false"] { padding-right: 10px; }
SpinBox:read-only { background: %(subtle_surface)s; }
SpinBox:focus { background: %(surface)s; border-color: %(accent)s; }
SpinBox:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
SpinBox QLineEdit { border: none; padding: 0; min-height: 0; background: transparent;
    selection-background-color: %(selection)s; selection-color: %(selection_text)s; }
''' % c
    if kind == 'spin-button':
        return '''
SpinButton, SpinButton:focus, SpinButton:disabled {
    background: transparent; border: 1px solid transparent; border-radius: 4px;
    padding: 0; min-height: 21px; min-width: 29px;
}
SpinButton:hover:enabled { background: %(hover)s; }
SpinButton:pressed:enabled { background: %(raised)s; }
SpinButton:focus:enabled { border-color: %(accent)s; }
''' % c
    if kind == 'combo':
        return '''
ComboBox { color: %(ink)s; background: %(surface)s; border: 1px solid %(button_border)s;
    border-bottom-color: %(control_border)s;
    border-radius: 6px; padding: 5px 30px 5px 10px; min-height: 20px; combobox-popup: 0; }
ComboBox:hover { background: %(subtle_surface)s; border-color: %(control_border)s; }
ComboBox:focus, ComboBox:on { border: 1px solid %(accent)s; background: %(surface)s; }
ComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 30px;
    border: none; border-radius: 5px; background: transparent; }
ComboBox::drop-down:hover:enabled { background: %(hover)s; }
ComboBox::drop-down:on { background: %(notice_bg)s; }
ComboBox:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
ComboBox QComboBoxPrivateContainer { background: %(surface)s; border: none; }
ComboBox QAbstractItemView { color: %(ink)s; background: %(surface)s;
    border: 1px solid %(button_border)s; border-radius: 6px; selection-background-color: %(row_selected)s;
    selection-color: %(ink)s; padding: 3px; outline: none; }
ComboBox QAbstractItemView::item { border: 1px solid transparent; border-radius: 4px; padding: 5px 8px; }
ComboBox QAbstractItemView::item:hover { background: %(hover)s; }
ComboBox QAbstractItemView::item:selected, ComboBox QAbstractItemView::item:selected:hover {
    background: %(row_selected)s; color: %(ink)s; }
ComboBox QAbstractItemView::item:disabled { color: %(disabled)s; }
''' % c
    if kind == 'segments':
        return '''
SegmentedWidget { background: transparent; border: none; }
SegmentedItem { background: transparent; border: 1px solid transparent; border-radius: 5px; color: %(muted)s; }
SegmentedItem:hover { background: %(raised)s; color: %(ink)s; }
SegmentedItem[isSelected="true"], SegmentedItem:checked { background: transparent; color: %(accent)s; font-weight: 600; }
SegmentedItem:focus, SegmentedItem:checked:focus { border-color: %(accent)s; }
SegmentedItem:disabled, SegmentedItem:checked:disabled { color: %(disabled)s; }
''' % c
    raise ValueError('Unknown control style: ' + kind)
