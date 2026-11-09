"""WorkBox Studio: presentation tokens, never a second theme manager."""

DESIGN_NAME = 'WorkBox Studio'
CONTROL_RADIUS = 8

PALETTES = {
    'light': {
        'bg': '#F3F2EF', 'surface': '#FFFFFF', 'raised': '#EEEDF3',
        'subtle_surface': '#F8F7F5', 'ink': '#242532', 'muted': '#4E4E5D',
        'placeholder': '#626272', 'border': '#DFDEE6', 'button_border': '#C9C7D4',
        'control_border': '#898696', 'accent': '#4F46C8', 'accent_hover': '#4338B1',
        'accent_pressed': '#362B93', 'accent_text': '#FFFFFF',
        'hover': '#EFEDF8', 'selection': '#4F46C8', 'selection_text': '#FFFFFF',
        'row_selected': '#ECEAFB', 'row_selected_border': '#B7B0E7',
        'disabled': '#70707D', 'disabled_bg': '#F0EFF3', 'disabled_check': '#606171',
        'scroll_handle': '#92909F', 'scroll_hover': '#696579',
        'state_new': '#565767', 'state_working': '#3C55A7', 'state_pending': '#87500D',
        'state_delivered': '#0E7054', 'error': '#AB3045',
        'notice_bg': '#EFEDFC', 'info_bg': '#EBF0FB', 'success_bg': '#EAF5EF',
        'warning_bg': '#FFF2DE', 'error_bg': '#FCECF0',
        'nav_bg': '#F8F7F5', 'nav_ink': '#2B2A39', 'nav_muted': '#565767',
        'nav_hover': '#EFEDF8', 'nav_selected': '#EAE7FB', 'nav_accent': '#473BB7',
        'orb_accent': '#4F46C8', 'orb_glow': '#E3DFF9', 'progress_fill': '#C9C4F5',
    },
    'dark': {
        'bg': '#19191F', 'surface': '#24242D', 'raised': '#30303B',
        'subtle_surface': '#202027', 'ink': '#F0EFF7', 'muted': '#C2BFCE',
        'placeholder': '#ACA8BA', 'border': '#3D3B4B', 'button_border': '#555166',
        'control_border': '#888097', 'accent': '#B4AEFF', 'accent_hover': '#C6C1FF',
        'accent_pressed': '#A39AEC', 'accent_text': '#242034',
        'hover': '#34313F', 'selection': '#7965C6', 'selection_text': '#FFFFFF',
        'row_selected': '#393249', 'row_selected_border': '#706185',
        'disabled': '#AAA5B6', 'disabled_bg': '#2C2A35', 'disabled_check': '#AAA5B6',
        'scroll_handle': '#898294', 'scroll_hover': '#B7B0C4',
        'state_new': '#C2BFCE', 'state_working': '#A9C2FF', 'state_pending': '#F1C27C',
        'state_delivered': '#86D5B4', 'error': '#FFB0C0',
        'notice_bg': '#363047', 'info_bg': '#2B334A', 'success_bg': '#243E35',
        'warning_bg': '#443525', 'error_bg': '#492E3B',
        'nav_bg': '#202027', 'nav_ink': '#F0EFF7', 'nav_muted': '#C2BFCE',
        'nav_hover': '#34313F', 'nav_selected': '#393249', 'nav_accent': '#C4BDFF',
        'orb_accent': '#B4AEFF', 'orb_glow': '#393249', 'progress_fill': '#554878',
    },
}


def palette_overrides(mode):
    return dict(PALETTES[mode])


def chrome(c):
    """Final shared layer: standard Qt states and layout remain authoritative."""
    return '''
/* WorkBox Studio — paper, graphite and iris */
QMainWindow, QDialog, QWidget#manager { background: %(bg)s; }
QWidget#workbenchCanvas { background: %(surface)s; }
QFrame#topWorkspaceBar { background: %(nav_bg)s; border-bottom: 1px solid %(border)s; }
QFrame#topWorkspaceBar QLabel#brandName { color: %(nav_ink)s; font-size: 16px; font-weight: 700; }
QFrame#workbenchHeader, QWidget#deliveryCommandBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
QFrame#workbenchFooter { background: %(subtle_surface)s; border-top: 1px solid %(border)s; }
QWidget#projectSurface { background: %(subtle_surface)s; border: none; }
QWidget#documentSurface { background: %(surface)s; border: none; }
QLabel#queueHeader { color: %(ink)s; font-size: 15px; font-weight: 600; }
QLabel#selectedProjectName { color: %(ink)s; font-size: 18px; font-weight: 600; }
QPushButton, QToolButton, QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit { border-radius: 8px; }
QFrame#dialogSection { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; }
QFrame#dialogActions, QFrame#actionBar { background: transparent; border: none; border-top: 1px solid %(border)s; }
QLabel#headerContext { color: %(accent)s; font-size: 11px; font-weight: 600; }
QFrame#metricStrip { background: %(subtle_surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#metricValue { font-size: 22px; font-weight: 600; }
QLabel#statusBanner { border-radius: 8px; }
QHeaderView::section { background: %(subtle_surface)s; color: %(muted)s; }
QMenu { border-radius: 10px; padding: 5px; }
QMenu::item { border-radius: 6px; padding: 7px 28px 7px 10px; }
QMenu::item:selected { color: %(ink)s; background: %(row_selected)s; }
QToolTip { background: %(surface)s; color: %(ink)s; border: 1px solid %(button_border)s; padding: 7px 10px; }
QProgressBar { background: %(raised)s; color: %(ink)s; selection-color: %(ink)s; border-radius: 4px; }
QProgressBar::chunk { background: %(progress_fill)s; border-radius: 4px; }
QTableWidget#checklistTable { background: %(surface)s; alternate-background-color: %(subtle_surface)s;
    border: 1px solid %(border)s; border-radius: 10px; gridline-color: %(border)s; }
QFrame#checklistMapping { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }
QLabel#checklistEmpty { color: %(muted)s; padding: 24px; }
''' % c
