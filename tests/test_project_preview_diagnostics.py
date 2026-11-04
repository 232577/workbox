"""Explicit failed previews leave a bounded local diagnostic, never business mutations."""
import json

from desktop_service import DesktopService


def test_blocked_preview_records_ids_states_and_reason_without_project_names(tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    source, target = service.receive_text('1. 来源示例系统\n2. 保留示例系统')['project_ids']
    service.bulk_update_projects([source], status='待交付')
    service.bulk_update_projects([target], archived=True)
    before = service.db.counts()
    plan = service.preview_project_rename(source, '保留示例系统', target)
    assert plan['kind'] == 'blocked'
    path = service.db.path.parent/'project-preview-failure.json'
    assert path.is_file()
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['source_id'] == source and data['target_id'] == target
    assert data['source_status'] == '待交付'
    assert data['warning'] == plan['warning'] and '归档' in data['warning']
    assert data['schema'] == 1 and data['at']
    assert service.db.counts() == before
    assert not any(name in path.read_text(encoding='utf-8') for name in ('来源示例系统','保留示例系统'))
    service.bulk_update_projects([target], archived=False)
    assert service.preview_project_rename(source, '保留示例系统', target)['kind'] == 'merge'
    assert json.loads(path.read_text(encoding='utf-8')) == data


def test_unwritable_diagnostic_never_changes_the_blocked_preview(tmp_path):
    service = DesktopService(tmp_path/'data/workbox.db')
    source, target = service.receive_text('1. 来源示例系统\n2. 保留示例系统')['project_ids']
    service.bulk_update_projects([target], archived=True)
    (service.db.path.parent/'project-preview-failure.json').mkdir()
    plan = service.preview_project_rename(source, '保留示例系统', target)
    assert plan['kind'] == 'blocked' and '归档' in plan['warning']
    assert 'diagnostic_warning' in plan


def test_bugfix_build_is_distinct_from_the_unreleased_first_candidate():
    from update_client import CURRENT_TAG, build_key
    assert build_key(CURRENT_TAG) > build_key('v1.0.0.20261104.1')
