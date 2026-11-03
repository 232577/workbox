"""A new UI binary must be distinguishable from the installed bootstrap build."""
import json

import update_client
from main import main


def test_ui_release_can_upgrade_the_online_update_bootstrap():
    assert update_client.build_key(update_client.CURRENT_TAG) > update_client.build_key('v1.0.0.20261102.1')


def test_build_probe_reports_release_without_creating_business_data(tmp_path):
    data = tmp_path / 'unused-data'
    result = tmp_path / 'identity.json'
    assert main(['--build-info', '--build-info-output', str(result), '--data-dir', str(data)]) == 0
    identity = json.loads(result.read_text(encoding='utf-8'))
    assert identity['release_tag'] == update_client.CURRENT_TAG
    assert identity['version'] == '1.0.0'
    assert not data.exists()
