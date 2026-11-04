"""WorkBox control chrome, layered over native Qt/Fluent behavior."""


def control_style(mode, kind):
    from theme import tokens
    c = tokens(mode)
    if kind == 'button':
        return '''
PushButton, PrimaryPushButton, ToggleButton {
    color: %(ink)s; background: %(surface)s; border: 1px solid %(border)s;
    border-radius: 6px; padding: 6px 12px; min-height: 18px; font-size: 13px;
}
PushButton:hover, ToggleButton:hover { background: %(subtle_surface)s; border-color: %(control_border)s; }
PushButton:pressed, ToggleButton:pressed { background: %(raised)s; }
PushButton:focus, ToggleButton:focus { border: 2px solid %(accent)s; padding: 5px 11px; }
PushButton[workboxRole="secondary"] { background: transparent; border-color: transparent; color: %(muted)s; }
PushButton[workboxRole="secondary"]:hover { background: %(raised)s; color: %(ink)s; border-color: transparent; }
PushButton[workboxRole="secondary"]:focus { border-color: %(accent)s; color: %(ink)s; }
PushButton:disabled, PrimaryPushButton:disabled, ToggleButton:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: transparent;
}
PrimaryPushButton, PrimaryPushButton#primary {
    color: %(accent_text)s; background: %(accent)s; border-color: %(accent)s; font-weight: 600;
}
PrimaryPushButton:hover, PrimaryPushButton#primary:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
PrimaryPushButton:pressed, PrimaryPushButton#primary:pressed { background: %(accent)s; }
PrimaryPushButton:focus, PrimaryPushButton#primary:focus { border: 2px solid %(ink)s; padding: 5px 11px; }
PrimaryPushButton:disabled, PrimaryPushButton#primary:disabled {
    color: %(disabled)s; background: %(disabled_bg)s; border-color: transparent;
}
QPushButton#nav, QToolButton#nav {
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    color: %(nav_ink)s; padding: 8px 12px; min-height: 20px; text-align: left; font-weight: 500;
}
QToolButton#nav { padding-left: 10px; }
QPushButton#nav:hover, QToolButton#nav:hover { background: %(nav_hover)s; color: %(nav_ink)s; }
QPushButton#nav:checked { background: %(selection)s; color: %(selection_text)s; font-weight: 600; }
QPushButton#nav[softSelection="true"]:checked {
    background: %(nav_selected)s; color: %(nav_accent)s; border-color: transparent; font-weight: 600;
}
QPushButton#nav:focus, QToolButton#nav:focus { border: 2px solid %(nav_accent)s; padding: 7px 11px; }
QToolButton#nav:focus { padding-left: 9px; }
QPushButton#nav[compactNav="true"], QToolButton#nav[compactNav="true"] { text-align: center; padding-left: 8px; padding-right: 8px; }
QPushButton#nav[compactCount="true"] { padding-bottom: 22px; }
QPushButton#nav[compactCount="true"]:focus { padding-bottom: 21px; }
''' % c
    if kind == 'line':
        return '''
LineEdit, LineEdit:focus, LineEdit:disabled {
    min-height: 30px; max-height: 32px; padding: 0px 10px; border-radius: 6px;
    color: %(ink)s; background: %(surface)s; border: 1px solid %(control_border)s;
}
LineEdit:hover { background: %(subtle_surface)s; }
LineEdit:focus { border: 1px solid %(accent)s; background: %(surface)s; }
LineEdit:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
''' % c
    if kind == 'combo':
        return '''
ComboBox { color: %(ink)s; background: %(surface)s; border: 1px solid %(control_border)s;
    border-radius: 6px; padding: 6px 30px 6px 10px; min-height: 18px; }
ComboBox:hover { background: %(subtle_surface)s; }
ComboBox:focus { border: 2px solid %(accent)s; padding: 5px 29px 5px 9px; }
ComboBox:disabled { color: %(disabled)s; background: %(disabled_bg)s; border-color: %(border)s; }
ComboBox QAbstractItemView { color: %(ink)s; background: %(surface)s;
    border: 1px solid %(border)s; selection-background-color: %(notice_bg)s;
    selection-color: %(ink)s; padding: 4px; outline: none; }
''' % c
    if kind == 'segments':
        return '''
SegmentedWidget { background: transparent; border: none; }
SegmentedItem { background: transparent; border: none; border-radius: 6px; color: %(muted)s; }
SegmentedItem:hover { background: %(raised)s; color: %(ink)s; }
SegmentedItem[isSelected="true"], SegmentedItem:checked { background: transparent; color: %(accent)s; font-weight: 600; }
''' % c
    raise ValueError('Unknown control style: ' + kind)
