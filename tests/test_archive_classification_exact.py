"""归档分类比较方式及旧库兼容，使用匿名临时数据。"""
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from archive_classification import ArchiveClassification
from delivery_export import collect_delivery_report, export_delivered_projects
from desktop_service import DesktopService
from test_archive_classification import current, type_id, zip_event


@pytest.fixture
def service(tmp_path):
    box = DesktopService(tmp_path / 'data' / 'workbox.db')
    box.receive_text('1. 匿名甲管理系统\n2. 匿名乙管理系统')
    return box


def database_dump(db):
    with db.connect() as connection:
        return tuple(connection.iterdump())


@pytest.mark.parametrize('count,expected', [(0, '生成材料'), (5, '生成材料'), (15, '生成材料'),
                                           (16, '可运行'), (17, '生成材料'), (None, None)])
def test_fresh_policy_matches_exactly_sixteen_known_files(service, count, expected):
    classifier = service.archive_classification
    db = service.db
    custom = db.create_project_type('匿名手工类型')['id']
    db.set_project_types([1], custom)
    zip_event(db, count)
    preview = classifier.preview()
    row = preview['rows'][0]
    assert row['count'] == count
    assert row['new_type_name'] == (expected or '')
    assert row['eligible'] is (expected is not None)
    classifier.apply(preview['token'], confirmed=True, enable_future=False)
    assert current(db) == (type_id(db, expected) if expected else custom)


def test_old_policy_migrates_without_changing_settings_or_history(service):
    db = service.db
    classifier = service.archive_classification
    custom = db.create_project_type('匿名旧类型')['id']
    low = type_id(db, '生成材料')
    classifier.set_policy(True, 7, custom, low)
    with db.connect() as connection:
        # 旧表没有比较字段，模拟在未启动新分类服务前，由交付事务读取旧库。
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(archive_classification_policy)')}
        if 'comparison' in columns:
            connection.execute('ALTER TABLE archive_classification_policy DROP COLUMN comparison')
        connection.execute('UPDATE archive_classification_policy SET initial_event_id=1, revision=9')
    zip_event(db, 8, hook=True)
    assert current(db) == low
    zip_event(db, 7, hook=True)
    assert current(db) == custom
    before = database_dump(db)
    assert classifier.policy()['comparison'] == 'at_least'
    assert database_dump(db) == before
    migrated = ArchiveClassification(db)
    assert migrated.policy() == dict(id=1, enabled=True, minimum_files=7, high_type_id=custom,
                                     low_type_id=low, initial_event_id=1, revision=9, comparison='at_least')
    after = database_dump(db)
    assert [line for line in before if 'archive_classification_policy' not in line] == [
        line for line in after if 'archive_classification_policy' not in line]
    assert ArchiveClassification(db).policy() == migrated.policy()
    assert database_dump(db) == after
    migrated.set_policy(True, 7, custom, low, comparison='exact')
    zip_event(db, 8, hook=True, path='Z:/anonymous/migrated.zip')
    assert current(db) == low
    zip_event(db, 7, hook=True, path='Z:/anonymous/custom-exact.zip')
    assert current(db) == custom


def test_legacy_policy_call_retains_at_least_semantics(service):
    db = service.db
    classifier = service.archive_classification
    assert classifier.set_policy(True)['comparison'] == 'at_least'
    zip_event(db, 5, hook=True)
    assert current(db) == type_id(db, '可运行')


@pytest.mark.parametrize('comparison', ['greater_than', None, [], 1])
def test_invalid_comparison_does_not_write(service, comparison):
    before = database_dump(service.db)
    with pytest.raises(ValueError, match='比较方式'):
        service.archive_classification.set_policy(True, 16, comparison=comparison)
    assert database_dump(service.db) == before


def test_comparison_change_invalidates_preview_even_when_target_stays_same(service):
    db = service.db
    classifier = service.archive_classification
    zip_event(db, 16)
    before = classifier.preview()
    # 单独改变比较方式，验证比较方式自身进入令牌，不依赖版本号或目标变化。
    with db.connect() as connection:
        connection.execute("UPDATE archive_classification_policy SET comparison='at_least'")
    after = classifier.preview()
    assert before['rows'] == after['rows']
    assert before['token'] != after['token']
    with pytest.raises(ValueError, match='过期'):
        classifier.apply(before['token'], confirmed=True)


@pytest.mark.parametrize('counts,types', [((7, 9), ('生成材料', '生成材料')),
                                        ((16, 5), ('可运行', '生成材料'))])
def test_zip_auto_delivery_and_per_project_type_are_independent(service, tmp_path, counts, types):
    service.archive_classification.set_policy(True, 16, comparison='exact')
    names = ['匿名甲管理系统', '匿名乙管理系统']
    package = tmp_path / f'{names[0]}-2-G.zip'
    with ZipFile(package, 'w') as archive:
        for name, count in zip(names, counts):
            for number in range(count):
                archive.writestr(f'{name}/{number}.txt', '匿名归档')
    service.import_zip(package)
    assert [service.get_project(pid)['status'] for pid in (1, 2)] == ['已交付', '已交付']
    rows = sorted(collect_delivery_report(service.db)['projects'], key=lambda row: row['project_id'])
    assert [(row['archive_file_count'], row['project_type_name']) for row in rows] == list(zip(counts, types))


def test_redelivery_reclassifies_and_export_reads_current_type(service, tmp_path):
    db = service.db
    classifier = service.archive_classification
    classifier.set_policy(True, 16, comparison='exact')
    workspace = Path(service.workspaces.get_workspace(1)['path'])
    for number in range(16):
        (workspace / f'{number}.txt').write_text(f'匿名文件 {number}', encoding='utf-8')
    first = service.confirm_delivery(1, confirmed=True)['event_id']
    assert current(db) == type_id(db, '可运行')
    for number in range(5, 16):
        (workspace / f'{number}.txt').unlink()
    latest = service.confirm_delivery(1, confirmed=True)['event_id']
    assert current(db) == type_id(db, '生成材料')
    db.void_delivery_events([latest], confirmed=True)
    assert classifier.preview()['rows'][0]['event_id'] == first
    assert current(db) == type_id(db, '生成材料')
    db.restore_delivery_events([latest], confirmed=True)
    assert classifier.preview()['rows'][0]['event_id'] == latest
    # 手工类型由表格原样导出，避免导出隐式重分类。
    custom = db.create_project_type('匿名手工类型')['id']
    service.set_project_types([1], custom)
    before = database_dump(db)
    target = tmp_path / 'anonymous.xlsx'
    export_delivered_projects(db, target)
    workbook = load_workbook(target, read_only=True)
    try:
        values = list(workbook['交付项目'].values)
        assert values[1][1:3] == ('匿名手工类型', 5)
        assert list(workbook['类型汇总'].values)[1][:2] == ('匿名手工类型', 1)
    finally:
        workbook.close()
    assert database_dump(db) == before
    with db.connect() as connection:
        audited = [json.loads(row['facts_json']) for row in connection.execute(
            "SELECT facts_json FROM archive_classification_actions WHERE kind='auto' ORDER BY id")]
    assert [fact['count'] for fact in audited] == [16, 5]
    assert all((fact['policy']['comparison'], fact['policy']['minimum_files']) == ('exact', 16)
               for fact in audited)


def test_exact_batch_undo_respects_same_value_manual_type_selection(service):
    db = service.db
    classifier = service.archive_classification
    zip_event(db, 16)
    zip_event(db, 16, project_id=2)
    batch = classifier.apply(classifier.preview()['token'], confirmed=True, enable_future=False)
    assert batch['changed_project_ids'] == [1, 2]
    with db.connect() as connection:
        audited = [json.loads(row['facts_json']) for row in connection.execute(
            'SELECT facts_json FROM archive_classification_actions WHERE operation_id=?', (batch['operation_id'],))]
    assert len(audited) == 2
    assert all((fact['policy']['comparison'], fact['policy']['minimum_files']) == ('exact', 16)
               for fact in audited)
    service.set_project_types([1], type_id(db, '可运行'))
    result = classifier.undo(batch['operation_id'], confirmed=True)
    assert result['changed_project_ids'] == [2]
    assert result['skipped'][0]['project_id'] == 1
    assert current(db) == type_id(db, '可运行')
    assert current(db, 2) == type_id(db, '生成材料')
