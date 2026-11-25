"""共享的办公室账号、服务器地址和 Kanboard 资料页核对逻辑。"""

from dataclasses import dataclass
from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from workbox.team_delivery.contracts import GatewayError


_PROFILE_PATH = '/?controller=UserViewController&action=show'
_AUTHORITY = re.compile(r'(?:\[[0-9a-fA-F:.]+\]|[a-zA-Z0-9.-]+)(?::[0-9]+)?')
_DNS_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
_BROWSER_IP_SUFFIX = re.compile(r'(?:[0-9]+|0x[0-9a-f]*)')


_PROFILE_LINK = r'''
    const menu = 'header .menus-container .dropdown > ul > li > a[href], ' +
        'header.wb-simple-header > details.wb-simple-account > div > a[href]';
    const ownLabels = ['个人信息', '个人资料', 'My profile'];
    function profileId(root, selector, labels, baseUrl) {
        const links = [...root.querySelectorAll(selector)]
            .filter(link => labels.includes(link.textContent.trim()));
        if (links.length !== 1) return null;
        const url = new URL(links[0].getAttribute('href'), baseUrl);
        if (url.origin !== origin || url.username || url.password || url.hash ||
            !['/', '/index.php'].includes(url.pathname)) return null;
        const params = url.searchParams;
        if (params.getAll('controller').join(',') !== 'UserViewController' ||
            params.getAll('action').join(',') !== 'show' || params.has('plugin')) return null;
        const ids = params.getAll('user_id');
        return ids.length === 1 && /^[1-9][0-9]*$/.test(ids[0]) ? ids[0] : null;
    }
'''

_READ_PROFILE = r'''
async ({origin, path, timeout}) => {
    if (location.origin !== origin) return false;
''' + _PROFILE_LINK + r'''
    const profileUrl = origin + path;
    const response = await fetch(profileUrl, {
        method: 'GET',
        mode: 'same-origin',
        credentials: 'same-origin',
        redirect: 'manual',
        cache: 'no-store',
        signal: AbortSignal.timeout(timeout),
    });
    if (response.type === 'opaqueredirect' || response.status === 401) return null;
    if (!response.ok || response.url !== profileUrl) return false;

    // 模板内容保持离线，不执行资料页脚本或加载资源。
    const template = document.createElement('template');
    template.innerHTML = await response.text();
    const profile = template.content;
    if (profile.querySelector('.form-login')) return null;

    const menuId = profileId(profile, menu, ownLabels, profileUrl);
    const sidebarId = profileId(profile, '#main #user-section > .sidebar > ul > li > a[href]',
                                ['概要', 'Summary'], profileUrl);
    const pageId = profileId(document, menu, ownLabels, location.href);
    if (!menuId || menuId !== sidebarId || menuId !== pageId) return false;

    const rows = [...profile.querySelectorAll('#main #user-section > .sidebar-content > ul.panel > li')];
    const loginLabels = ['用户名：', '用户名:', 'Login:'];
    const loginRows = rows.filter(row => row.firstChild?.nodeType === Node.TEXT_NODE &&
        loginLabels.includes(row.firstChild.textContent.trim()));
    if (loginRows.length !== 1 || loginRows[0].children.length !== 1) return false;
    const strong = loginRows[0].firstElementChild;
    if (strong.localName !== 'strong' || !strong.textContent.trim()) return false;
    return {id: menuId, username: strong.textContent};
}
'''


@dataclass(frozen=True)
class WorkBoxAccount:
    origin: str
    user_id: str
    username: str
    session: str = ''


def normalize_workbox_url(url: str) -> str:
    """接受明确的 HTTP(S) 根地址，并按浏览器原点规范返回。"""
    error = '请输入 WorkBox 首页地址，例如 http://workbox 或 https://workbox.example。'
    if not isinstance(url, str):
        raise GatewayError(error)
    url = url.strip()
    if re.search(r'[\\?#\s\x00-\x1f\x7f]', url):
        raise GatewayError(error)

    try:
        address = urlsplit(url)
        port = address.port
        if (address.scheme not in ('http', 'https') or address.path not in ('', '/')
                or not _AUTHORITY.fullmatch(address.netloc) or port == 0):
            raise ValueError
        host = address.hostname
        try:
            ip = ip_address(host)
        except ValueError:
            labels = host.removesuffix('.').split('.')
            if (len(host) > 253 or any(not _DNS_LABEL.fullmatch(label) for label in labels)
                    or _BROWSER_IP_SUFFIX.fullmatch(labels[-1])):
                raise ValueError from None
        else:
            host = f'[{ip}]' if ip.version == 6 else str(ip)
    except ValueError:
        raise GatewayError(error) from None

    default_port = 80 if address.scheme == 'http' else 443
    suffix = '' if port in (None, default_port) else f':{port}'
    return f'{address.scheme}://{host}{suffix}'
