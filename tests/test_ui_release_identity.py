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


def test_studio_installer_is_newer_than_the_published_r7():
    # r7 predates PR68. Reusing its identity would prevent installed clients
    # from discovering the already-integrated Studio/checklist runtime.
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261109.7')


def test_window_lifecycle_studio_build_does_not_reuse_the_earlier_draft():
    # The r1 draft already identifies the candidate built before PR73.
    # A new payload must be independently identifiable, even for draft testers.
    assert update_client.build_key(update_client.CURRENT_TAG) >= update_client.build_key('v1.0.0.20261110.2')
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261110.1')


def test_studio_and_global_hotkey_build_has_its_own_identity():
    # The r2 candidate was frozen before PR75. Preserve both old draft payloads
    # and let their users discover this integrated Studio/window/hotkey build.
    assert update_client.build_key(update_client.CURRENT_TAG) >= update_client.build_key('v1.0.0.20261110.3')
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261110.2')


def test_verified_installer_does_not_reuse_concurrent_r3_artifacts():
    # Another machine already uploaded r3 with a different runtime payload.
    # Keep that draft immutable and distinguish this independently verified build.
    assert update_client.build_key(update_client.CURRENT_TAG) >= update_client.build_key('v1.0.0.20261110.4')
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261110.3')


def test_audit_fixes_are_distinct_from_the_existing_r4_candidate():
    assert update_client.build_key(update_client.CURRENT_TAG) >= update_client.build_key('v1.0.0.20261111.1')
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261110.4')


def test_app_name_intake_build_can_upgrade_the_published_audit_release():
    assert update_client.build_key(update_client.CURRENT_TAG) >= update_client.build_key('v1.0.0.20261112.1')
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261111.1')


def test_bulk_urgency_build_can_upgrade_the_published_name_intake_release():
    assert update_client.CURRENT_TAG == 'v1.0.0.20261112.2'
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261112.1')


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
    ('v1.0.0.20261110.1', 'v1.0.0.20261109.7', 'WorkBox-1.0.0-Setup-x64-20261110-r1.exe'),
    ('v1.0.0.20261110.2', 'v1.0.0.20261109.7', 'WorkBox-1.0.0-Setup-x64-20261110-r2.exe'),
    ('v1.0.0.20261110.2', 'v1.0.0.20261110.1', 'WorkBox-1.0.0-Setup-x64-20261110-r2.exe'),
    ('v1.0.0.20261110.3', 'v1.0.0.20261109.7', 'WorkBox-1.0.0-Setup-x64-20261110-r3.exe'),
    ('v1.0.0.20261110.3', 'v1.0.0.20261110.2', 'WorkBox-1.0.0-Setup-x64-20261110-r3.exe'),
    ('v1.0.0.20261110.4', 'v1.0.0.20261109.7', 'WorkBox-1.0.0-Setup-x64-20261110-r4.exe'),
    ('v1.0.0.20261110.4', 'v1.0.0.20261110.3', 'WorkBox-1.0.0-Setup-x64-20261110-r4.exe'),
    ('v1.0.0.20261111.1', 'v1.0.0.20261110.3', 'WorkBox-1.0.0-Setup-x64-20261111-r1.exe'),
    ('v1.0.0.20261111.1', 'v1.0.0.20261110.4', 'WorkBox-1.0.0-Setup-x64-20261111-r1.exe'),
    ('v1.0.0.20261112.1', 'v1.0.0.20261111.1', 'WorkBox-1.0.0-Setup-x64-20261112-r1.exe'),
    ('v1.0.0.20261112.2', 'v1.0.0.20261112.1', 'WorkBox-1.0.0-Setup-x64-20261112-r2.exe'),
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
