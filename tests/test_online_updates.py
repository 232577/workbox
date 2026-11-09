"""Online-update trust boundaries; no real downloads or live data in tests."""
import hashlib
import importlib
import io
import json
import os
import sqlite3
import threading
from concurrent.futures import CancelledError
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from update_client import CURRENT_TAG, build_key


# Transport/security fixtures stay newer than the binary; ordering tests below
# use explicit historical tags independently of this rolling fixture.
_next_day = datetime.strptime(str(build_key(CURRENT_TAG)[0]), '%Y%m%d') + timedelta(days=1)
FIXTURE_TAG = 'v1.0.0.' + _next_day.strftime('%Y%m%d') + '.1'


@pytest.fixture
def client():
    assert (Path(__file__).resolve().parents[1] / 'update_client.py').is_file(), 'Missing verified update client'
    return importlib.import_module('update_client')


def payload(data=b'MZisolated installer', tag=FIXTURE_TAG):
    suffix = tag.split('.', 3)[3].replace('.', '-r')
    name = f'WorkBox-1.0.0-Setup-x64-{suffix}.exe'
    return dict(tag_name=tag, draft=False, prerelease=False, body='匿名发行说明',
                html_url=f'https://github.com/232577/workbox/releases/tag/{tag}',
                assets=[dict(name=name, size=len(data), state='uploaded',
                    digest='sha256:' + hashlib.sha256(data).hexdigest(),
                    browser_download_url=f'https://github.com/232577/workbox/releases/download/{tag}/{name}')])


def test_valid_release_and_same_version_are_distinguished(client):
    record = payload(tag='v1.0.0.20261103.1')
    release = client.parse_release(record, current_tag='v1.0.0.20261102.1')
    assert release.tag == 'v1.0.0.20261103.1'
    assert release.notes == '匿名发行说明'
    assert client.parse_release(record, current_tag=release.tag) is None
    assert client.parse_release(record, current_tag='v1.0.0.20261104') is None


@pytest.mark.parametrize('tag', ['v1.0.0.20261301', 'v1.0.0.20260230', 'v1.0.0.20261102.0',
    'v1.0.0.20261102/evil', 'v1.0.0.20261102.01', 'v1.0.0-enhanced.20261028', 'v2.0.0.20261103'])
def test_unsupported_release_identifiers_fail_closed(client, tag):
    with pytest.raises(ValueError):
        client.parse_release(payload(tag=tag))


@pytest.mark.parametrize('field,value', [('draft', True), ('prerelease', True), ('assets', []), ('draft', 'false')])
def test_invalid_release_states(client, field, value):
    item = payload(); item[field] = value
    with pytest.raises(ValueError): client.parse_release(item)


@pytest.mark.parametrize('field,value', [('size', True), ('size', 0), ('size', 300*1024*1024),
    ('digest', None), ('digest', 'sha256:'+'0'*63), ('state', 'new'),
    ('browser_download_url', 'https://github.com/evil/workbox/releases/download/x/setup.exe'),
    ('browser_download_url', 'http://github.com/232577/workbox/a.exe')])
def test_asset_metadata_is_validated(client, field, value):
    item = payload(); item['assets'][0][field] = value
    with pytest.raises(ValueError): client.parse_release(item)


def test_ambiguous_assets_rejected(client):
    item = payload(); item['assets'].append(dict(item['assets'][0]))
    with pytest.raises(ValueError): client.parse_release(item)


class Response(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(data); self.url=url; self.headers=headers or {}; self.status=200
    def geturl(self): return self.url


def test_download_reuses_verified_cache_and_reports_progress(client, tmp_path, monkeypatch):
    data = b'MZisolated installer'; release=client.parse_release(payload(data))
    monkeypatch.setattr(client, '_open', lambda url: Response(data, url))
    progress=[]
    first=client.download_release(release,tmp_path,progress=lambda done,total:progress.append((done,total)))
    monkeypatch.setattr(client, '_open', lambda url: pytest.fail('verified cache needs no network'))
    monkeypatch.setattr(client.shutil, 'disk_usage', lambda root: pytest.fail('cache needs no extra disk space'))
    second=client.download_release(release,tmp_path,progress=lambda done,total:progress.append((done,total)))
    assert first == second and first.read_bytes() == data
    assert progress[-1] == (len(data),len(data))
    client.verify_installer(first,release)


@pytest.mark.parametrize('change', ['size', 'header', 'digest', 'unknown', 'result', 'partial', 'name'])
def test_cache_reuse_skips_untrusted_or_claimed_entries(client, tmp_path, monkeypatch, change):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client, '_open', lambda url: Response(data,url))
    first=client.download_release(release,tmp_path)
    if change == 'size': first.write_bytes(data+b'!')
    elif change == 'header': first.write_bytes(b'XX'+data[2:])
    elif change == 'digest': first.write_bytes(b'MZ'+b'x'*(len(data)-2))
    elif change == 'name':
        renamed=first.parent.with_name('user-downloads');first.parent.rename(renamed);first=renamed/first.name
    else:
        extra={'unknown':'notes.txt','result':'install-result.json','partial':'download.part'}[change]
        (first.parent/extra).write_bytes(b'preserve')
    before={p:p.read_bytes() for p in first.parent.iterdir()}
    second=client.download_release(release,tmp_path)
    assert first != second and second.read_bytes() == data
    assert all(p.read_bytes()==content for p,content in before.items())


def test_cache_reuse_rechecks_candidate_path_boundaries(client,tmp_path,monkeypatch):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    first=client.download_release(release,tmp_path)
    original=client.plain_path; rejected=[]
    def reject_candidate(path, **kwargs):
        if Path(path) == first.parent:
            rejected.append(path)
            raise ValueError('isolated reparse point')
        return original(path,**kwargs)
    monkeypatch.setattr(client,'plain_path',reject_candidate)
    second=client.download_release(release,tmp_path)
    assert rejected and second != first and first.read_bytes()==data


def test_cache_verification_honors_cancellation(client,tmp_path,monkeypatch):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    first=client.download_release(release,tmp_path)
    event=threading.Event();original=client.verify_installer
    def cancel_after_verify(path, item, **kwargs):
        original(path,item,**kwargs);event.set()
    monkeypatch.setattr(client,'verify_installer',cancel_after_verify)
    with pytest.raises(CancelledError):
        client.download_release(release,tmp_path,cancel_event=event)
    assert first.read_bytes()==data and len(list(tmp_path.iterdir()))==1


def test_cache_reuse_rechecks_handoff_claim_after_hashing(client,tmp_path,monkeypatch):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    first=client.download_release(release,tmp_path)
    original=client.verify_installer
    def claim_after_verify(path, item, **kwargs):
        original(path,item,**kwargs)
        if path == first:
            (first.parent/'install-result.json').write_text('preserve',encoding='utf-8')
    monkeypatch.setattr(client,'verify_installer',claim_after_verify)
    second=client.download_release(release,tmp_path)
    assert second != first and (first.parent/'install-result.json').read_text()=='preserve'


@pytest.mark.skipif(os.name!='nt',reason='Windows junction boundary')
@pytest.mark.parametrize('linked_root',[False,True])
def test_cache_reuse_never_follows_directory_junction(client,tmp_path,monkeypatch,linked_root):
    import _winapi
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    cache=tmp_path/'cache';external=tmp_path/'external';external.mkdir();cache.mkdir()
    installer=external/release.filename;installer.write_bytes(data)
    linked=tmp_path/'linked-cache' if linked_root else cache/('a'*32)
    _winapi.CreateJunction(str(external),str(linked))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    if linked_root:
        with pytest.raises(ValueError):client.download_release(release,linked)
    else:
        assert client.download_release(release,cache).parent != linked
    assert list(external.iterdir())==[installer] and installer.read_bytes()==data


def test_cache_reuse_never_accepts_hardlinked_installer(client,tmp_path,monkeypatch):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    first=client.download_release(release,tmp_path/'cache')
    external=tmp_path/'user-installer.exe';os.link(first,external)
    second=client.download_release(release,tmp_path/'cache')
    assert second != first and external.read_bytes()==first.read_bytes()==data


@pytest.mark.skipif(os.name!='nt',reason='Windows download-origin stream')
@pytest.mark.parametrize('origin',[None,'[ZoneTransfer]\nZoneId=0\n'])
def test_cache_reuse_does_not_downgrade_internet_origin(client,tmp_path,monkeypatch,origin):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    first=client.download_release(release,tmp_path)
    stream=Path(str(first)+':Zone.Identifier')
    if origin is None:stream.unlink()
    else:stream.write_text(origin,encoding='utf-8')
    second=client.download_release(release,tmp_path)
    assert second != first and first.read_bytes()==data
    assert 'ZoneId=3' in Path(str(second)+':Zone.Identifier').read_text(encoding='utf-8')
    if origin is None:assert not stream.exists()
    else:assert stream.read_text(encoding='utf-8')==origin


def test_installer_hashing_can_be_cancelled_between_blocks(client,tmp_path,monkeypatch):
    data=b'MZ'+b'x'*(3*1024*1024);release=client.parse_release(payload(data))
    target=tmp_path/release.filename;target.write_bytes(data)
    event=threading.Event();original=client.hashlib.sha256
    class CancellingHash:
        def __init__(self):self.digest=original();self.updates=0
        def update(self,block):
            self.digest.update(block);self.updates+=1
            if self.updates==2:event.set()
        def hexdigest(self):return self.digest.hexdigest()
    monkeypatch.setattr(client.hashlib,'sha256',CancellingHash)
    with pytest.raises(CancelledError):client.verify_installer(target,release,cancel_event=event)
    assert target.read_bytes()==data


@pytest.mark.parametrize('data', [b'MZwrong', b'MZisolated installer trailing', b'XXisolated installer'])
def test_failed_download_leaves_no_executable_or_partial(client,tmp_path,monkeypatch,data):
    release=client.parse_release(payload())
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    with pytest.raises((ValueError,OSError)): client.download_release(release,tmp_path)
    assert not list(tmp_path.rglob('*.exe')) and not list(tmp_path.rglob('*.part'))


def test_cancelled_download_never_installs(client,tmp_path,monkeypatch):
    event=threading.Event();event.set()
    monkeypatch.setattr(client,'_open',lambda url:pytest.fail('cancel before network'))
    with pytest.raises(CancelledError):client.download_release(client.parse_release(payload()),tmp_path,cancel_event=event)
    assert not list(tmp_path.rglob('*.exe'))


@pytest.mark.parametrize('url',['http://github.com/x','https://github.com.evil.test/x',
    'https://user:password@github.com/x','https://localhost/x','file:///tmp/file',
    'https://release-assets.githubusercontent.com:8443/x'])
def test_untrusted_redirect_targets_rejected(client,url):
    with pytest.raises(ValueError):client.validate_transport_url(url)


def test_metadata_fetch_is_bounded_and_does_not_send_secrets(client,monkeypatch):
    called=[]
    def open_url(url): called.append(url);return Response(json.dumps(payload()).encode(),url)
    monkeypatch.setattr(client,'_open',open_url)
    assert client.check_for_update().tag == FIXTURE_TAG
    assert called==['https://workbox.ltxiangzi.dpdns.org/updates/stable.json']
    monkeypatch.setattr(client,'_open',lambda url:Response(b' '* (client.MAX_METADATA+1),url))
    with pytest.raises(ValueError):client.check_for_update()


def test_modified_cached_file_rejected(client,tmp_path):
    release=client.parse_release(payload());path=tmp_path/release.filename
    path.write_bytes(b'MZmodified installer')
    with pytest.raises(ValueError):client.verify_installer(path,release)


def test_database_backup_is_consistent_and_preserves_source(client,tmp_path):
    install=importlib.import_module('update_install')
    database=tmp_path/'data'/'workbox.db';database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE example(value)');db.execute('INSERT INTO example VALUES (?)',('preserve',))
    before=database.read_bytes()
    saved=install.backup_database(database,tmp_path/'backups')
    with sqlite3.connect(saved) as db:assert db.execute('SELECT value FROM example').fetchone()==('preserve',)
    assert database.read_bytes()==before and saved!=database


def test_handoff_is_encoded_and_waits_without_force_killing(client,tmp_path):
    import base64
    install=importlib.import_module('update_install')
    release=client.parse_release(payload());path=tmp_path/release.filename;path.write_bytes(b'MZisolated installer')
    app=tmp_path/"安装 ' path";app.mkdir();(app/'WorkBox.exe').write_bytes(b'fixture')
    args=install.handoff_command(path,release,app,1234)
    assert args[0].lower().endswith('powershell.exe') and '-EncodedCommand' in args
    script=base64.b64decode(args[-1]).decode('utf-16le')
    assert 'WaitForExit' in script and 'SHA256' in script and 'FileShare]::Read' in script
    assert 'Stop-Process' not in script and 'Invoke-Expression' not in script
    assert '/NORESTART' in script and '/NOCLOSEAPPLICATIONS' in script
    assert '/SILENT ' in script and '/WORKBOXUPDATE=1 /DIR=' in script
    assert '/VERYSILENT' not in script and '/SUPPRESSMSGBOXES' not in script


def test_site_manifest_avoids_anonymous_api_rate_limit(client,monkeypatch):
    from urllib.error import HTTPError
    site='https://workbox.ltxiangzi.dpdns.org/updates/stable.json'
    def transport(url):
        if url==site:return Response(json.dumps(payload()).encode(),url)
        raise HTTPError(url,403,'rate limit exceeded',{},None)
    monkeypatch.setattr(client,'_open',transport)
    assert client.check_for_update().tag == FIXTURE_TAG


def test_github_fallback_when_site_unavailable(client,monkeypatch):
    from urllib.error import URLError
    called=[]
    def transport(url):
        called.append(url)
        if 'dpdns.org' in url:raise URLError('isolated unavailable')
        return Response(json.dumps(payload()).encode(),url)
    monkeypatch.setattr(client,'_open',transport)
    assert client.check_for_update() is not None
    assert called==['https://workbox.ltxiangzi.dpdns.org/updates/stable.json',client.API_URL]


@pytest.mark.skipif(os.name!='nt',reason='Windows download-origin stream')
def test_download_retains_windows_internet_origin(client,tmp_path,monkeypatch):
    data=b'MZisolated installer';release=client.parse_release(payload(data))
    monkeypatch.setattr(client,'_open',lambda url:Response(data,url))
    result=client.download_release(release,tmp_path)
    origin=Path(str(result)+':Zone.Identifier').read_text(encoding='utf-8')
    assert 'ZoneId=3' in origin and 'HostUrl='+release.url in origin


def test_windowed_build_probe_writes_identity_without_opening_data(client,tmp_path,monkeypatch):
    import main
    data=tmp_path/'untouched custom data';report=tmp_path/'build-info.json'
    with monkeypatch.context() as patch:
        patch.setattr(main.sys,'stdout',None)
        assert main.main(['--build-info','--build-info-output',str(report),'--data-dir',str(data)])==0
    result=json.loads(report.read_text(encoding='utf-8'))
    assert result=={'version':'1.0.0','release_tag':client.CURRENT_TAG,'data_dir':str(data.resolve())}
    assert not data.exists()
    before=report.read_bytes()
    assert main.main(['--build-info','--build-info-output',str(report)])==1
    assert report.read_bytes()==before


def test_handoff_checks_installed_build_and_data_before_relaunch(client,tmp_path):
    import base64
    from update_install import handoff_command
    release=client.parse_release(payload());installer=tmp_path/release.filename;installer.write_bytes(b'MZisolated installer')
    app=tmp_path/'custom app';app.mkdir();(app/'WorkBox.exe').write_bytes(b'fixture')
    script=base64.b64decode(handoff_command(installer,release,app,1234,tmp_path/'custom data')[-1]).decode('utf-16le')
    assert '--build-info-output' in script and '$actual.release_tag' in script and '$actual.data_dir' in script
    assert script.index('$actual.release_tag') < script.index("'--panel --data-dir=")
    assert '$probe.WaitForExit(30000)' in script


def test_online_installer_hides_and_validates_original_target(client):
    script=(Path(__file__).resolve().parents[1]/'packaging/workbox.iss').read_text(encoding='utf-8')
    assert 'DisableDirPage=no' in script, 'ordinary manual installation remains configurable'
    assert 'function ShouldSkipPage(PageID: Integer): Boolean;' in script
    assert 'Result := IsOnlineUpdate and (PageID = wpSelectDir);' in script
    assert 'function PrepareToInstall(var NeedsRestart: Boolean): String;' in script
    assert 'CompareText(ExpandFileName' in script and 'OnlineTargetDir' in script
    assert 'WizardForm.DirEdit.Enabled := False;' in script
