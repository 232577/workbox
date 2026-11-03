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


def test_download_verified_unique_path_and_progress(client, tmp_path, monkeypatch):
    data = b'MZisolated installer'; release=client.parse_release(payload(data))
    monkeypatch.setattr(client, '_open', lambda url: Response(data, url))
    progress=[]
    first=client.download_release(release,tmp_path,progress=lambda done,total:progress.append((done,total)))
    second=client.download_release(release,tmp_path)
    assert first != second and first.read_bytes() == data == second.read_bytes()
    assert progress[-1] == (len(data),len(data))
    client.verify_installer(first,release)


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
