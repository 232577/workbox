"""A new UI binary must be distinguishable from the installed bootstrap build."""
import json
import pytest

import update_client
from main import main


def test_ui_release_can_upgrade_the_online_update_bootstrap():
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261102.1')


def test_completed_secondary_ui_is_newer_than_the_orb_fix_release():
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261103.3')


def test_integrated_delivery_is_newer_than_the_published_r4():
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261103.4')


def test_build_probe_reports_release_without_creating_business_data(tmp_path):
    data = tmp_path / 'unused-data'
    result = tmp_path / 'identity.json'
    assert main(['--build-info', '--build-info-output', str(result), '--data-dir', str(data)]) == 0
    identity = json.loads(result.read_text(encoding='utf-8'))
    assert identity['release_tag'] == update_client.CURRENT_TAG
    assert identity['version'] == '1.0.0'
    assert not data.exists()


@pytest.mark.parametrize('tag,previous,filename', [
    ('v1.0.0.20261109.6', 'v1.0.0.20261109.5', 'WorkBox-1.0.0-Setup-x64-20261109-r6.exe'),
    ('v1.0.0.20261109.7', 'v1.0.0.20261109.6', 'WorkBox-1.0.0-Setup-x64-20261109-r7.exe'),
])
def test_release_upgrades_previous_without_reoffering_installed_or_older(tag, previous, filename):
    payload = {'tag_name': tag, 'draft': False, 'prerelease': False,
               'html_url': 'https://github.com/232577/workbox/releases/tag/' + tag,
               'assets': [{'name': filename, 'size': 10, 'state': 'uploaded',
                           'digest': 'sha256:' + 'a' * 64,
                           'browser_download_url': f'https://github.com/232577/workbox/releases/download/{tag}/{filename}'}]}
    upgrade = update_client.parse_release(payload, current_tag=previous)
    assert upgrade is not None and upgrade.tag == tag and upgrade.filename == filename
    assert update_client.parse_release(payload) is None
