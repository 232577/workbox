import re

from compact_ui import STYLE


def contrast(a, b):
    def lum(color):
        rgb = [int(color[index:index+2], 16)/255 for index in (1, 3, 5)]
        linear = [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4 for value in rgb]
        return sum(v*w for v,w in zip(linear, (.2126, .7152, .0722)))
    first, second = sorted((lum(a), lum(b)))
    return (second+.05)/(first+.05)


def rule(selector):
    for match in re.finditer(r'([^{}]+)\{([^{}]+)\}', STYLE):
        if selector in [value.strip() for value in match[1].split(',')]:
            return dict(item.strip().split(':', 1) for item in match[2].split(';') if ':' in item)
    raise AssertionError('样式规则不存在：' + selector)


def test_secondary_text_is_readable_on_both_surfaces():
    text = rule('QLabel#subtle')['color']
    assert contrast(text, '#FFFFFF') >= 7
    assert contrast(text, rule('QListWidget')['background']) >= 7


def test_selection_and_form_boundaries_are_distinct():
    selected = rule('QListWidget::item:selected')
    background = rule('QListWidget')['background']
    assert contrast(selected['background'], background) >= 3
    assert contrast(selected['color'], selected['background']) >= 4.5
    border = re.search(r'#[0-9A-Fa-f]{6}', rule('QLineEdit')['border'])[0]
    assert contrast(border, '#FFFFFF') >= 3
