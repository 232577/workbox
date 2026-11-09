"""Semantic design contract; shared colors are not per-page literals."""
import pytest
from theme import tokens, stylesheet


def contrast(a, b):
    def light(v):
        c = [int(v[i:i+2], 16)/255 for i in (1, 3, 5)]
        c = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in c]
        return sum(x*w for x,w in zip(c, (.2126,.7152,.0722)))
    x,y = sorted((light(a), light(b)))
    return (y+.05)/(x+.05)


@pytest.mark.parametrize('mode,accent', [('light','#4F46C8'), ('dark','#B4AEFF')])
def test_independent_studio_palette_has_accessible_states(mode, accent):
    c = tokens(mode)
    assert c['accent'] == accent
    for fg,bg in [('ink','surface'),('muted','surface'),('placeholder','surface'),('accent_text','accent'),('nav_accent','nav_selected'),('error','error_bg'),('state_pending','warning_bg')]:
        assert contrast(c[fg],c[bg]) >= 4.5, (mode,fg,bg)
    assert c['surface'] != c['bg']


@pytest.mark.parametrize('mode', ['light','dark'])
def test_shared_chrome_covers_main_dialog_table_feedback_and_native_controls(mode):
    text = stylesheet(mode)
    assert 'WorkBox Studio' in text
    for selector in ('#topWorkspaceBar','#projectSurface','#documentSurface','#dialogActions','#statusBanner','#checklistTable','QMenu','QProgressBar','QHeaderView','QToolTip'):
        assert selector in text


def test_token_copy_cannot_mutate_global_style():
    changed = tokens('light'); changed['accent'] = '#000000'
    assert tokens('light')['accent'] == '#4F46C8'


@pytest.mark.parametrize('mode', ['light','dark'])
def test_progress_percentage_uses_readable_foreground_on_filled_track(mode):
    from PySide6.QtWidgets import QApplication,QProgressBar
    from PySide6.QtGui import QPalette
    from theme import install_theme,set_theme
    app=QApplication.instance() or QApplication([])
    install_theme();set_theme(mode,persist=False)
    bar=QProgressBar();bar.setValue(65);bar.ensurePolished()
    try:
        color=bar.palette().color(QPalette.HighlightedText).name().upper()
        assert color==tokens(mode)['accent_text']
        assert contrast(color,tokens(mode)['accent'])>=4.5
    finally:bar.close();bar.deleteLater();app.processEvents();install_theme()
