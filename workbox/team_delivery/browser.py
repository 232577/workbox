"""Start a visible, isolated browser for a user-operated site."""

from pathlib import Path
import sys

from playwright.sync_api import Error as BrowserError

from workbox.team_delivery.contracts import GatewayError


def open_browser(driver):
    for channel in ('chrome', 'msedge'):
        try:
            return driver.chromium.launch(channel=channel, headless=False, chromium_sandbox=True)
        except BrowserError:
            continue
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        for executable in (Path(sys.executable).parent / 'Chromium' / 'chrome.exe',
                           Path(bundle) / 'chromium' / 'chrome.exe'):
            if not executable.is_file():
                continue
            try:
                return driver.chromium.launch(executable_path=str(executable), headless=False, chromium_sandbox=True)
            except BrowserError as error:
                raise GatewayError('内置浏览器未能启动，请关闭浏览器窗口后再试。') from error
    raise GatewayError('可用浏览器未找到，请用完整的 WorkBox 安装包修复。')
