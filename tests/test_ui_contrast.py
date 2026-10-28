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
            result = {key.strip(): value.strip() for key, value in
                      (item.split(':', 1) for item in match[2].split(';') if ':' in item)}
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
    border = re.search(r'#[0-9A-Fa-f]{6}', rule('QLineEdit', mode)['border'])[0]
    assert contrast(border, tokens(mode)['surface']) >= 3
