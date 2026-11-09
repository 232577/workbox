"""User-initiated updates from one HTTPS repository; never executes downloads."""
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import time
from urllib.parse import urlsplit
from urllib.error import URLError
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, build_opener
import uuid

from native_files import plain_path

CURRENT_TAG = 'v1.0.0.20261109.2'
API_URL = 'https://api.github.com/repos/232577/workbox/releases/latest'
MANIFEST_URL = 'https://workbox.ltxiangzi.dpdns.org/updates/stable.json'
RELEASE_ROOT = 'https://github.com/232577/workbox/releases/'
MAX_METADATA = 1024 * 1024
MAX_INSTALLER = 256 * 1024 * 1024
ALLOWED_HOSTS = frozenset({'workbox.ltxiangzi.dpdns.org', 'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                           'objects.githubusercontent.com'})


@dataclass(frozen=True)
class Release:
    tag: str
    filename: str
    size: int
    sha256: str
    url: str
    notes: str


def build_key(tag: str) -> tuple[int, int]:
    if not isinstance(tag, str):
        raise ValueError('发行版本格式无效。')
    match = re.fullmatch(r'v1\.0\.0\.(20\d{6})(?:\.([1-9]\d{0,3}))?', tag)
    if match is None:
        raise ValueError('此发行版本不支持在线升级，请查看官网发行说明。')
    datetime.strptime(match[1], '%Y%m%d')
    return int(match[1]), int(match[2] or 0)


def parse_release(payload: dict, *, current_tag: str = CURRENT_TAG) -> Release | None:
    if (not isinstance(payload, dict) or payload.get('draft') is not False
            or payload.get('prerelease') is not False):
        raise ValueError('拒绝草稿、预发布或不完整的发行记录。')
    tag = payload.get('tag_name')
    key = build_key(tag)
    current = build_key(current_tag)
    if payload.get('html_url') != RELEASE_ROOT + 'tag/' + tag:
        raise ValueError('发行记录不属于指定的 WorkBox 仓库。')
    if key <= current:
        return None
    suffix = str(key[0]) + (f'-r{key[1]}' if key[1] else '')
    filename = f'WorkBox-1.0.0-Setup-x64-{suffix}.exe'
    assets = payload.get('assets')
    if not isinstance(assets, list):
        raise ValueError('缺少安装包资产。')
    matches = [a for a in assets if isinstance(a, dict) and a.get('name') == filename]
    if len(matches) != 1:
        raise ValueError('未找到唯一的 Windows x64 正式安装包。')
    item = matches[0]
    size, digest = item.get('size'), item.get('digest')
    url = RELEASE_ROOT + f'download/{tag}/{filename}'
    if (type(size) is not int or not 2 <= size <= MAX_INSTALLER
            or item.get('state') != 'uploaded' or item.get('browser_download_url') != url
            or not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)):
        raise ValueError('安装包来源、大小或SHA-256信息无效，未允许安装。')
    notes = payload.get('body') or '此版本未提供发行说明。'
    if not isinstance(notes, str):
        raise ValueError('发行说明格式无效。')
    return Release(tag, filename, size, digest[7:], url, notes[:12000])


def validate_transport_url(url: str) -> None:
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS
            or parts.username is not None or parts.password is not None
            or parts.port not in (None, 443) or parts.fragment):
        raise ValueError('升级连接指向未允许的HTTPS来源。')


class _SafeRedirect(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_transport_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str):
    validate_transport_url(url)
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _SafeRedirect())
    request = Request(url, headers={'User-Agent': 'WorkBox-Update/1.0',
                                   'Accept': 'application/json' if url in (API_URL, MANIFEST_URL) else 'application/octet-stream'})
    response = opener.open(request, timeout=12)
    try:
        validate_transport_url(response.geturl())
        if response.status != 200:
            raise ValueError('更新服务器未返回完整文件。')
        return response
    except Exception:
        response.close()
        raise


def _cancel(event) -> None:
    if event is not None and event.is_set():
        raise CancelledError('已取消更新操作，现有版本未改变。')


def check_for_update(*, cancel_event=None, progress=None) -> Release | None:
    _cancel(cancel_event)
    data = None
    errors = []
    for url in (MANIFEST_URL, API_URL):
        _cancel(cancel_event)
        try:
            with _open(url) as response:
                data = response.read(MAX_METADATA + 1)
            break
        except (URLError, OSError) as error:
            errors.append(str(error)[:180])
    if data is None:
        raise OSError('官网和备用更新源暂不可用，现有版本未改变。' + ' / '.join(errors))
    _cancel(cancel_event)
    if len(data) > MAX_METADATA:
        raise ValueError('发行记录超过允许大小。')
    try:
        payload = json.loads(data.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('无法读取发行记录，未下载或安装任何文件。') from error
    return parse_release(payload)


def verify_installer(path: Path, release: Release) -> None:
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size != release.size:
        raise ValueError('安装包不存在或长度校验失败。')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        first = stream.read(2)
        if first != b'MZ':
            raise ValueError('下载内容不是Windows安装程序。')
        digest.update(first)
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != release.sha256:
        raise ValueError('安装包SHA-256校验失败，拒绝运行。')


def download_release(release: Release, cache_root: Path, *, cancel_event=None, progress=None) -> Path:
    _cancel(cancel_event)
    # Re-validate all fields even when called outside the UI.
    checked = parse_release(dict(tag_name=release.tag, draft=False, prerelease=False,
        html_url=RELEASE_ROOT+'tag/'+release.tag, body=release.notes,
        assets=[dict(name=release.filename, size=release.size, state='uploaded',
                     digest='sha256:'+release.sha256, browser_download_url=release.url)]))
    if checked is None:
        raise ValueError('不能下载同构建或旧构建作为升级。')
    root = plain_path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    root = plain_path(root)
    if shutil.disk_usage(root).free < release.size + 64 * 1024 * 1024:
        raise OSError('更新缓存所在磁盘空间不足。')
    folder = root / uuid.uuid4().hex
    folder.mkdir()
    partial = folder / (release.filename + '.part')
    final = folder / release.filename
    started, count = time.monotonic(), 0
    try:
        with _open(release.url) as response, partial.open('xb') as stream:
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdecimal() or int(declared) != release.size):
                raise ValueError('服务器声明的安装包长度不一致。')
            while True:
                _cancel(cancel_event)
                if time.monotonic() - started > 600:
                    raise TimeoutError('本次下载超时，可重新检查更新后重试。')
                block = response.read(256 * 1024)
                if not block:
                    break
                count += len(block)
                if count > release.size:
                    raise ValueError('下载内容超过发行记录中的大小。')
                stream.write(block)
                if progress is not None:
                    progress(count, release.size)
            stream.flush()
            os.fsync(stream.fileno())
        _cancel(cancel_event)
        verify_installer(partial, release)
        os.replace(partial, final)
        if os.name == 'nt':
            # Preserve Windows Internet-origin handling; never bypass OS security prompts.
            Path(str(final) + ':Zone.Identifier').write_text(
                '[ZoneTransfer]\nZoneId=3\nHostUrl=' + release.url + '\n', encoding='utf-8')
        return final
    except BaseException:
        partial.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
