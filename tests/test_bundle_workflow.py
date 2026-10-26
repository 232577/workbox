from pathlib import Path
from zipfile import ZipFile

import pytest

from desktop_service import DesktopService


NAMES = ['甲视觉检测平台', '乙资源调度平台']


def setup(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.receive_text('1. ' + NAMES[0] + '\n2. ' + NAMES[1])
    return service


def write_bundle(path, names=NAMES, payload='第一版'):
    with ZipFile(path, 'w') as archive:
        for name in names:
            archive.writestr(name + '/说明.txt', payload)
    return path


def test_bundle_drop_ignores_current_single_project_and_marks_both_after_stability(tmp_path):
    service = setup(tmp_path)
    source = write_bundle(tmp_path / (NAMES[0] + '-2-G.zip'))
    service.receive_file(source, project_id=2)
    service.scan_archives(now=0)
    rows = service.scan_archives(now=11)['archived']
    assert len(rows) == 1 and source.exists()
    assert service.get_project(1)['status'] == '待交付', '批量包只更新了当前选中的一个项目'
    assert service.get_project(2)['status'] == '待交付'
    assert service.get_project(1)['zips'][0]['id'] == rows[0]['id']
    assert service.get_project(2)['zips'][0]['id'] == rows[0]['id']
    assert service.db.counts()['zips'] == 1


def test_wrong_declared_count_never_changes_a_selected_projects_state(tmp_path):
    service = setup(tmp_path)
    source = write_bundle(tmp_path / (NAMES[0] + '-2-G.zip'), NAMES[:1])
    service.receive_file(source, project_id=2)
    service.scan_archives(now=0)
    row = service.scan_archives(now=11)['archived'][0]
    assert service.get_project(2)['status'] == '待开发', '数量不符时不能使用选中项目兜底标成完成'
    assert service.get_project(1)['status'] == '待开发'
    assert service.db.get_zip_bundle(row['id'])['state'] == 'needs-review'
    assert service.db.get_zip(row['id'])['project_id'] is None


def test_reinspection_uses_original_filename_and_fixes_old_single_association_without_copying(tmp_path):
    service = setup(tmp_path)
    source = write_bundle(tmp_path / (NAMES[0] + '-2-G.zip'))
    record = service.import_zip(source)['zip']
    assert hasattr(service.snapshots, 'recognize_bundle'), '缺少已归档批量包重新识别入口'
    with service.db.connect() as connection:
        connection.execute('DELETE FROM zip_bundle_members WHERE zip_id=?', (record['id'],))
        connection.execute('DELETE FROM zip_bundles WHERE zip_id=?', (record['id'],))
        connection.execute('UPDATE zips SET project_id=2 WHERE id=?', (record['id'],))
        connection.execute("UPDATE projects SET status='待开发' WHERE id=1")
    before = Path(record['path']).read_bytes()
    result = service.snapshots.recognize_bundle(record['id'])
    assert result['bundle']['project_ids'] == [1, 2]
    assert service.get_project(1)['status'] == service.get_project(2)['status'] == '待交付'
    assert service.db.counts()['zips'] == 1
    assert service.db.get_zip(record['id'])['path'] == record['path']
    assert Path(record['path']).read_bytes() == before and source.exists()


def test_shared_archive_supports_independent_delivery_and_accepted_versions(tmp_path):
    service = setup(tmp_path)
    shared = service.import_zip(write_bundle(tmp_path / (NAMES[0] + '-2-G.zip')))['zip']
    assert service.get_project(1)['zips'], '共享包未出现在第一个项目'
    service.snapshots.approve_version(shared['id'])
    service.confirm_delivery(2, shared['id'], confirmed=True)
    delivered = service.get_project(2)
    single_path = tmp_path / (NAMES[0] + '.zip')
    with ZipFile(single_path, 'w') as archive:
        archive.writestr('说明.txt', '甲的新单项目版')
    single = service.import_zip(single_path)['zip']
    service.snapshots.approve_version(single['id'])
    assert [r['id'] for r in service.snapshots.list_versions(1) if r['approved']] == [single['id']]
    assert [r['id'] for r in service.snapshots.list_versions(2) if r['approved']] == [shared['id']]
    assert service.get_project(2)['delivered_zip_id'] == delivered['delivered_zip_id']
    assert service.get_project(2)['delivered_at'] == delivered['delivered_at']


def test_existing_bundle_cannot_be_reassigned_to_one_project(tmp_path):
    service = setup(tmp_path)
    shared = service.import_zip(write_bundle(tmp_path / (NAMES[0] + '-2-G.zip')))['zip']
    with pytest.raises(ValueError):
        service.link_zip(shared['id'], 2)


def test_new_bundle_recounts_each_project_and_preserves_old_archive(tmp_path):
    service = setup(tmp_path)
    source = write_bundle(tmp_path / (NAMES[0] + '-2-G.zip'))
    old = service.import_zip(source)['zip']
    assert service.get_project(2)['zips'], '共享包未出现在第二个项目'
    service.confirm_delivery(2, old['id'], confirmed=True)
    write_bundle(source, payload='第二版')
    service.scan_archives(now=0)
    latest = service.scan_archives(now=11)['archived'][0]
    assert latest['id'] != old['id']
    assert service.get_project(2)['status'] == '待交付'
    assert service.get_project(2)['delivered_zip_id'] is None
    assert service.get_project(2)['delivered_at'] is None
    assert len(service.get_project(1)['zips']) == len(service.get_project(2)['zips']) == 2
    assert Path(old['path']).exists() and source.exists()


def test_legacy_managed_bundle_cannot_move_or_gain_a_third_project(tmp_path):
    service = setup(tmp_path)
    service.capture_message('丙无关平台', '另一个批次')
    original = write_bundle(tmp_path / (NAMES[0] + '-2-G.zip'))
    legacy = service.archive.import_zip(original, project_id=1)['zip']
    assert hasattr(service.snapshots, 'recognize_bundle')
    service.snapshots.recognize_bundle(legacy['id'])
    before = Path(legacy['path']).read_bytes()
    with pytest.raises(ValueError):
        service.link_zip(legacy['id'], 3)
    assert service.db.zip_project_ids(legacy['id']) == [1, 2]
    assert service.db.get_zip(legacy['id'])['path'] == legacy['path']
    assert Path(legacy['path']).read_bytes() == before
    assert service.get_project(3)['status'] == '待开发'
