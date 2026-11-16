"""增量下载只使用匿名程序目录、受限包和内存网络响应。"""
from concurrent.futures import CancelledError
from dataclasses import replace
import hashlib
from http.client import HTTPResponse
import importlib
import io
import json
import os
from pathlib import Path
import sys
import threading
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
import update_client as client


FROM = client.CURRENT_TAG
TO = 'v1.0.0.20991231.1'
DELTA_NAME = f'WorkBox-1.0.0-Delta-x64-{FROM.split(".", 3)[3].replace(".", "-r")}-to-20991231-r1.wbdelta'
FULL_NAME = 'WorkBox-1.0.0-Setup-x64-20991231-r1.exe'
FULL_DATA = b'MZ' + b'anonymous full installer' * 400


def sha(data):
    return hashlib.sha256(data).hexdigest()


def asset(name, data):
    return dict(name=name, size=len(data), digest='sha256:' + sha(data), state='uploaded',
                browser_download_url=client.RELEASE_ROOT + f'download/{TO}/{name}')


def record(delta=None):
    return dict(tag_name=TO, html_url=client.RELEASE_ROOT + 'tag/' + TO,
        draft=False, prerelease=False, body='匿名发布说明',
        assets=[asset(FULL_NAME, FULL_DATA)] + ([delta] if delta is not None else []))


def module():
    assert (Path(__file__).resolve().parents[1] / 'incremental_update.py').is_file(), '缺少增量下载器'
    return importlib.import_module('incremental_update')


def test_release_six_fields_remain_compatible_and_matching_delta_is_optional():
    old = client.Release(TO, FULL_NAME, len(FULL_DATA), sha(FULL_DATA), 'https://example.invalid', 'notes')
    assert old.delta is None
    parsed = client.parse_release(record(asset(DELTA_NAME, b'anonymous delta')))
    assert parsed.delta.from_tag == FROM and parsed.delta.to_tag == TO
    assert parsed.delta.filename == DELTA_NAME and parsed.delta.size == len(b'anonymous delta')
    assert parsed.delta.sha256 == sha(b'anonymous delta')
    assert client.parse_release(record()).delta is None


@pytest.mark.parametrize('field,value', [('size', True), ('size', 0), ('size', len(FULL_DATA)),
    ('size', len(FULL_DATA) + 1), ('digest', None), ('digest', 'sha256:' + 'A' * 64),
    ('state', 'new'), ('browser_download_url', 'http://github.com/a'),
    ('browser_download_url', client.RELEASE_ROOT + f'download/{FROM}/{DELTA_NAME}'),
    ('name', DELTA_NAME.replace('20991231-r1', '20991230-r1'))])
def test_bad_optional_delta_does_not_damage_full_release(field, value):
    optional = asset(DELTA_NAME, b'anonymous delta'); optional[field] = value
    parsed = client.parse_release(record(optional))
    assert parsed.filename == FULL_NAME and parsed.delta is None


def test_duplicate_delta_is_ignored_but_invalid_full_is_never_accepted():
    payload = record(asset(DELTA_NAME, b'anonymous delta'))
    payload['assets'].append(dict(payload['assets'][1]))
    assert client.parse_release(payload).delta is None
    payload['assets'][0]['digest'] = 'bad'
    with pytest.raises(ValueError):
        client.parse_release(payload)


def test_direct_release_with_bad_optional_digest_still_keeps_valid_full():
    release = client.parse_release(record(asset(DELTA_NAME, b'anonymous delta')))
    corrupted = replace(release, delta=replace(release.delta, sha256=None))
    checked = module()._validated_release(corrupted)
    assert checked.delta is None and checked.filename == FULL_NAME


def test_full_reverification_delegates_without_requiring_new_release_fields(tmp_path, monkeypatch):
    release = object(); path = tmp_path / 'old-installer.exe'; called = []
    monkeypatch.setattr(client, 'verify_installer', lambda p, r: called.append((p, r)))
    module().verify_downloaded_update(path, release, tmp_path / 'app')
    assert called == [(path, release)]


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    app = tmp_path / 'anonymous-app'; app.mkdir()
    base_data = {'WorkBox.exe': b'MZanonymous runtime', 'WorkBoxUpdater.exe': b'MZanonymous helper',
        '_internal/python311.dll': b'python', '_internal/PySide6/Qt6Core.dll': b'qt',
        '_internal/PySide6/plugins/platforms/qwindows.dll': b'platform',
        'PACKAGE_CONTENTS.sha256': b'anonymous package list'}
    target_data = dict(base_data, **{'WorkBox.exe': b'MZupdated anonymous runtime'})
    base = {name: dict(sha256=sha(data), size=len(data)) for name, data in base_data.items()}
    target = {name: dict(sha256=sha(data), size=len(data)) for name, data in target_data.items()}
    manifest = dict(format='workbox-file-delta-v1', from_tag=FROM, to_tag=TO, base=base, target=target)
    for name, data in base_data.items():
        path = app / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    content = io.BytesIO()
    with ZipFile(content, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('delta.json', json.dumps(manifest))
        archive.writestr('payload/WorkBox.exe', target_data['WorkBox.exe'])
    data = content.getvalue()
    release = client.parse_release(record(asset(DELTA_NAME, data)))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(app / 'WorkBox.exe'))
    calls = []
    def transport(url):
        calls.append(url)
        response = io.BytesIO(data if url.endswith('.wbdelta') else FULL_DATA)
        response.headers = {}
        return response
    monkeypatch.setattr(client, '_open', transport)
    return app, release, data, calls


def test_valid_delta_download_and_reverification_preserve_data(candidate, tmp_path):
    app, release, data, calls = candidate
    data_dir = app / 'data'; data_dir.mkdir(); sentinel = data_dir / 'business.db'; sentinel.write_bytes(b'preserve')
    downloader = module(); statuses = []; progress = []
    path = downloader.download_update(release, tmp_path / 'cache', app_dir=app,
        status=statuses.append, progress=lambda done, total: progress.append((done, total)))
    assert path.name == DELTA_NAME and path.read_bytes() == data
    assert calls == [release.delta.url] and progress[-1] == (len(data), len(data))
    downloader.verify_downloaded_update(path, release, app)
    assert sentinel.read_bytes() == b'preserve'
    assert any('增量' in value for value in statuses)


@pytest.mark.parametrize('reason', ['source', 'helper', 'no-delta'])
def test_unavailable_capability_downloads_only_full(candidate, tmp_path, monkeypatch, reason):
    app, release, _, calls = candidate
    if reason == 'source': monkeypatch.delattr(sys, 'frozen')
    elif reason == 'helper': (app / 'WorkBoxUpdater.exe').unlink()
    else: release = replace(release, delta=None)
    path = module().download_update(release, tmp_path / 'cache', app_dir=app)
    assert path.name == FULL_NAME and path.read_bytes() == FULL_DATA
    assert calls == [release.url]
    module().verify_downloaded_update(path, release, app)


@pytest.mark.parametrize('damage', ['baseline', 'extra', 'hardlink', 'bad-package', 'network'])
def test_delta_failure_falls_back_same_target_and_keeps_inputs(candidate, tmp_path, monkeypatch, damage):
    app, release, _, calls = candidate
    if damage == 'baseline': (app / '_internal/python311.dll').write_bytes(b'tampered')
    elif damage == 'extra': (app / '_internal/extra.dll').write_bytes(b'preserve extra')
    elif damage == 'hardlink': os.link(app / '_internal/python311.dll', tmp_path / 'user-copy.dll')
    if damage in ('bad-package', 'network'):
        original = client._open
        def transport(url):
            if url.endswith('.wbdelta'):
                if damage == 'network': raise OSError('anonymous offline')
                response = io.BytesIO(b'not the expected delta'); response.headers = {}; return response
            return original(url)
        monkeypatch.setattr(client, '_open', transport)
    before = {str(path.relative_to(app)): path.read_bytes() for path in app.rglob('*') if path.is_file()}
    statuses = []
    path = module().download_update(release, tmp_path / 'cache', app_dir=app, status=statuses.append)
    assert path.name == FULL_NAME and path.read_bytes() == FULL_DATA
    assert calls[-1] == release.url
    assert any('正在改用完整安装包' in text for text in statuses)
    assert before == {str(path.relative_to(app)): path.read_bytes() for path in app.rglob('*') if path.is_file()}


def test_truncated_http_chunk_falls_back_once_and_cleans_partial(candidate, tmp_path, monkeypatch):
    app, release, _, calls = candidate
    original_open, original_full = client._open, client.download_release
    full_calls = []

    class FakeSocket:
        def makefile(self, *args, **kwargs):
            # HTTPResponse 实际解析 chunked：声明 5 字节，网络在 abc 后截断。
            return io.BytesIO(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nabc')

    def transport(url):
        if url == release.delta.url:
            calls.append(url)
            response = HTTPResponse(FakeSocket())
            response.begin()
            return response
        return original_open(url)

    def full(*args, **kwargs):
        full_calls.append(args[0].tag)
        return original_full(*args, **kwargs)

    monkeypatch.setattr(client, '_open', transport)
    monkeypatch.setattr(client, 'download_release', full)
    cache = tmp_path / 'cache'; statuses = []
    path = module().download_update(release, cache, app_dir=app, status=statuses.append)
    assert full_calls == [TO] and calls == [release.delta.url, release.url]
    assert path.name == FULL_NAME and path.read_bytes() == FULL_DATA
    assert not list(cache.rglob('*.part')) and not list(cache.rglob('*.wbdelta'))
    assert sum('正在改用完整安装包' in value for value in statuses) == 1


@pytest.mark.parametrize('when', ['before', 'during'])
def test_cancel_never_falls_back_to_full(candidate, tmp_path, monkeypatch, when):
    app, release, _, calls = candidate
    event = threading.Event()
    if when == 'before': event.set()
    def progress(*_): event.set()
    with pytest.raises(CancelledError):
        module().download_update(release, tmp_path / 'cache', app_dir=app,
            cancel_event=event, progress=progress)
    assert release.url not in calls
    assert not list((tmp_path / 'cache').rglob('*.wbdelta'))


def test_pre_shutdown_reverification_rejects_changed_baseline_or_delta(candidate, tmp_path):
    app, release, _, _ = candidate
    downloader = module()
    path = downloader.download_update(release, tmp_path / 'cache', app_dir=app)
    original = (app / 'WorkBox.exe').read_bytes()
    (app / 'WorkBox.exe').write_bytes(b'MZchanged')
    with pytest.raises(ValueError): downloader.verify_downloaded_update(path, release, app)
    (app / 'WorkBox.exe').write_bytes(original)
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ValueError): downloader.verify_downloaded_update(path, release, app)


@pytest.mark.parametrize('damage', ['unknown', 'claimed', 'hardlink', 'origin'])
def test_delta_cache_reuse_skips_polluted_attempts(candidate, tmp_path, damage):
    app, release, _, calls = candidate
    downloader = module(); cache = tmp_path / 'cache'
    first = downloader.download_update(release, cache, app_dir=app)
    if damage == 'unknown': (first.parent / 'extra.txt').write_bytes(b'preserve')
    elif damage == 'claimed': (first.parent / 'install-result.json').write_bytes(b'preserve')
    elif damage == 'hardlink': os.link(first, tmp_path / 'user-delta.wbdelta')
    elif os.name == 'nt': Path(str(first) + ':Zone.Identifier').unlink()
    else: pytest.skip('Windows download-origin stream')
    before = {path: path.read_bytes() for path in first.parent.iterdir()}
    second = downloader.download_update(release, cache, app_dir=app)
    assert first != second and calls == [release.delta.url, release.delta.url]
    assert all(path.read_bytes() == content for path, content in before.items())


def test_verified_delta_cache_keeps_internet_origin_and_needs_no_network(candidate, tmp_path, monkeypatch):
    app, release, _, _ = candidate
    downloader = module(); cache = tmp_path / 'cache'
    first = downloader.download_update(release, cache, app_dir=app)
    origin = Path(str(first) + ':Zone.Identifier')
    before = origin.read_bytes() if os.name == 'nt' else None
    monkeypatch.setattr(client, '_open', lambda _: pytest.fail('verified cache needs no network'))
    assert downloader.download_update(release, cache, app_dir=app) == first
    if before is not None: assert origin.read_bytes() == before and b'ZoneId=3' in before


def test_failed_delta_attempt_never_removes_an_unowned_file(candidate, tmp_path, monkeypatch):
    app, release, _, _ = candidate
    downloader = module(); cache = tmp_path / 'cache'; inserted = []
    # 此用例隔离下载文件所有权；格式和基线的真实验证由上面的端到端用例覆盖。
    monkeypatch.setattr(downloader, '_read_delta', lambda *a, **k: None)
    def introduce_collision(*_):
        folder, = cache.iterdir()
        unknown = folder / release.delta.filename
        unknown.write_bytes(b'preserve unknown file')
        inserted.append(unknown)
    with pytest.raises(ValueError):
        downloader._download_delta(release, cache, app, progress=introduce_collision)
    assert inserted and inserted[0].read_bytes() == b'preserve unknown file'


def test_unresolved_update_blocks_delta_and_full_without_network(candidate, tmp_path, monkeypatch):
    app, release, _, calls = candidate
    apply = importlib.import_module('incremental_apply')
    monkeypatch.setattr(apply, 'has_pending_update', lambda _: True)
    for value in (release, replace(release, delta=None)):
        with pytest.raises(RuntimeError, match='恢复'):
            module().download_update(value, tmp_path / 'cache', app_dir=app)
    assert calls == [] and not (tmp_path / 'cache').exists()


@pytest.mark.parametrize('result', ['rolled-back', 'recovery-required', 'failed'])
def test_failed_same_delta_receipt_uses_full_on_next_attempt(candidate, tmp_path, result):
    app, release, _, calls = candidate
    downloader = module(); cache = tmp_path / 'cache'
    first = downloader.download_update(release, cache, app_dir=app)
    receipt = first.parent / 'install-result.json'
    receipt.write_text(json.dumps(dict(kind='delta', release_tag=TO,
        delta_sha256=release.delta.sha256, status=result)), encoding='utf-8')
    before = receipt.read_bytes(); statuses = []
    second = downloader.download_update(release, cache, app_dir=app, status=statuses.append)
    assert second.name == FULL_NAME and calls == [release.delta.url, release.url]
    assert receipt.read_bytes() == before and first.exists()
    assert any('正在改用完整安装包' in value for value in statuses)


@pytest.mark.parametrize('recorded', ['bad-json', 'oversize', 'cancelled', 'success', 'other-tag', 'other-sha', 'other-kind'])
def test_unrelated_or_unreadable_receipt_does_not_blacklist_delta(candidate, tmp_path, recorded):
    app, release, _, calls = candidate
    downloader = module(); cache = tmp_path / 'cache'
    first = downloader.download_update(release, cache, app_dir=app)
    content = dict(kind='delta', release_tag=TO, delta_sha256=release.delta.sha256, status='failed')
    if recorded == 'cancelled': content['status'] = 'cancelled'
    elif recorded == 'success': content['status'] = 'verified-relaunch-requested'
    elif recorded == 'other-tag': content['release_tag'] = FROM
    elif recorded == 'other-sha': content['delta_sha256'] = '0' * 64
    elif recorded == 'other-kind': content['kind'] = 'full'
    raw = 'not json' if recorded == 'bad-json' else json.dumps(content)
    if recorded == 'oversize': raw += ' ' * (16 * 1024)
    receipt = first.parent / 'install-result.json'; receipt.write_text(raw, encoding='utf-8')
    second = downloader.download_update(release, cache, app_dir=app)
    assert second.name == DELTA_NAME and second != first
    assert calls == [release.delta.url, release.delta.url]
    assert receipt.read_text(encoding='utf-8') == raw
