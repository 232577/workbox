import re
import pytest

from theme import stylesheet, tokens


def contrast(a, b):
    def lum(color):
        rgb = [int(color[index:index+2], 16)/255 for index in (1, 3, 5)]
        linear = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4 for value in rgb]
        return sum(v*w for v,w in zip(linear, (.2126, .7152, .0722)))
    first, second = sorted((lum(a), lum(b)))
    return (second+.05)/(first+.05)


def rule(selector, mode):
    # 基础主题可能定义同一选择器；核对最后的WorkBox语义层实际覆盖值。
    result = None
    style = stylesheet(mode).split('/* WorkBox semantic appearance */')[-1]
    for match in re.finditer(r'([^{}]+)\{([^{}]+)\}', style):
        if selector in [value.strip() for value in match[1].split(',')]:
            if result is None:
                result = {}
            result.update({key.strip(): value.strip() for key, value in
                           (item.split(':', 1) for item in match[2].split(';') if ':' in item)})
    if result is None:
        raise AssertionError('样式规则不存在：' + selector)
    return result


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_secondary_text_is_readable_on_both_surfaces(mode):
    text = rule('QLabel#subtle', mode)['color']
    assert contrast(text, tokens(mode)['bg']) >= 7
    assert contrast(text, rule('QListWidget', mode)['background']) >= 7


@pytest.mark.parametrize('mode', ['light', 'dark'])
def test_selection_and_form_boundaries_are_distinct(mode):
    selected = rule('QAbstractItemView::item:selected', mode)
    background = rule('QListWidget', mode)['background']
    assert contrast(selected['background'], background) >= 3
    assert contrast(selected['color'], selected['background']) >= 4.5
    field = rule('QLineEdit', mode)
    border = field.get('border-bottom-color') or re.search(r'#[0-9A-Fa-f]{6}', field['border'])[0]
    assert contrast(border, tokens(mode)['surface']) >= 3


@pytest.mark.parametrize('mode', ['light', 'dark'])
@pytest.mark.parametrize('fluent', [False, True])
def test_idle_input_renders_the_distinct_bottom_boundary(mode, fluent):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QVBoxLayout, QWidget
    from theme import install_theme, set_theme
    from ui_components import LineEdit

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    install_theme()
    set_theme(mode, persist=False)
    window = QWidget()
    layout = QVBoxLayout(window)
    field = (LineEdit if fluent else QLineEdit)()
    field.setPlaceholderText('搜索项目')
    elsewhere = QPushButton('其他操作')
    layout.addWidget(field)
    layout.addWidget(elsewhere)
    window.resize(300, 140)
    window.show()
    window.activateWindow()
    try:
        elsewhere.setFocus(Qt.TabFocusReason)
        app.processEvents()
        assert not field.hasFocus()
        captured = field.grab()
        image = captured.toImage()
        bottom = image.pixelColor(image.width() // 2, image.height() - 1)
        assert bottom == QColor(tokens(mode)['control_border'])
        assert contrast(bottom.name(), tokens(mode)['surface']) >= 3
    finally:
        window.close()
        set_theme('light', persist=False)
