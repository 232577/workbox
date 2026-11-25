from workbox.team_delivery.live_site import DesktopBrowserRuntime
from workbox.team_delivery.workbox_identity import WorkBoxBrowser

from test_workbox_identity import server


def _select(page, link):
    page.get_by_role('link', name=link).click()


def test_account_changes_between_reads_invalidate_the_old_confirmation(server):
    origin, _, _, _ = server
    browser = WorkBoxBrowser(origin)
    try:
        browser.open_login()
        _select(browser._page, '模拟登录甲')
        first = browser.current_account()
        _select(browser._page, '查看别人')
        assert browser.current_account() == first

        _select(browser._page, '模拟登录乙')
        _select(browser._page, '模拟登录甲')
        second = browser.current_account()
        assert second.user_id == first.user_id
        assert second != first

        _select(browser._page, '模拟退出')
        _select(browser._page, '模拟登录甲')
        third = browser.current_account()
        assert third.user_id == first.user_id
        assert third.session and third.session not in (first.session, second.session)
    finally:
        browser.close()


def test_reopening_the_same_account_uses_a_new_window_session(server):
    origin, _, _, _ = server
    runtime = DesktopBrowserRuntime()
    first_browser = WorkBoxBrowser(origin, runtime=runtime)
    second_browser = WorkBoxBrowser(origin, runtime=runtime)
    try:
        first_browser.open_login()
        second_browser.open_login()
        _select(first_browser._page, '模拟登录甲')
        first = first_browser.current_account()
        assert second_browser.current_account() is None
        _select(second_browser._page, '模拟登录甲')
        second = second_browser.current_account()
        assert second.user_id == first.user_id
        assert second != first

        first_browser.close()
        first_browser.open_login()
        _select(first_browser._page, '模拟登录甲')
        reopened = first_browser.current_account()
        assert reopened.user_id == first.user_id
        assert reopened.session not in (first.session, second.session)
        assert second_browser.current_account() == second
    finally:
        first_browser.close()
        second_browser.close()
        runtime.close()
