"""Independent BOX54 behavior checks. All examples are anonymous metadata."""
import copy
import importlib
from pathlib import Path

import pytest


def engine():
    assert (Path(__file__).resolve().parents[1] / 'material_matching.py').is_file(), (
        'material-check implementation is not present in this source commit')
    return importlib.import_module('material_matching')


def requirement(e, key='manual', family='word', count=1, keywords='说明书'):
    return e.Requirement(key, '匿名材料', family, count, keywords)


def document(key='one', name='说明书.docx', **changes):
    row = dict(id=key, name=name, relative_path=name, kind='工作文件',
               exists=True, is_directory=False)
    row.update(changes)
    return row


def snapshot(*rows, **changes):
    value = dict(project_id=1, workspace_path='registered-workspace',
                 errors=[], documents=list(rows))
    value.update(changes)
    return value


@pytest.mark.parametrize('family,suffixes', [
    ('word', ('.docm', '.dotx', '.dotm', '.rtf', '.ott')),
    ('sheet', ('.xlsb', '.xltx', '.xltm', '.tsv', '.ods', '.ots')),
    ('slides', ('.pptm', '.pps', '.ppsx', '.ppsm', '.potx', '.potm', '.dps', '.otp')),
    ('text', ('.log', '.json', '.xml', '.yaml', '.yml', '.html', '.htm', '.ini', '.cfg')),
    ('image', ('.jpeg', '.tif', '.tiff', '.svg', '.heic')),
    ('video', ('.wmv', '.avi', '.mov', '.m4v', '.webm')),
    ('audio', ('.wav', '.m4a', '.aac', '.ogg', '.wma')),
    ('archive', ('.zip', '.rar')),
])
def test_advertised_box54_family_variants(family, suffixes):
    e = engine()
    for suffix in suffixes:
        result = e.match_materials([e.MaterialFile('one', '说明书' + suffix.upper())],
                                  [requirement(e, family=family)], '匿名系统')
        assert result.complete, (family, suffix)


@pytest.mark.parametrize('filename', ['说明书.docx.exe', '说明书.docx.lnk',
                                     '说明书.docx.zip', '说明书', '说明书.pdf'])
def test_misleading_suffix_never_satisfies_word(filename):
    e = engine()
    result = e.match_materials([e.MaterialFile('one', filename)], [requirement(e)], '匿名系统')
    assert not result.complete and result.rows[0].matched == 0


@pytest.mark.parametrize('filename', ['说明书．docx', '说明书.ｄｏｃｘ'])
def test_unicode_normalization_cannot_invent_a_file_extension(filename):
    """Normalize searchable text, not the filesystem's actual extension syntax."""
    e = engine()
    result = e.match_materials([e.MaterialFile('one', filename)], [requirement(e)], '匿名系统')
    assert not result.complete and result.rows[0].matched == 0


def test_fullwidth_separator_in_a_filename_is_not_an_actual_parent_directory():
    e = engine()
    result = e.match_materials([e.MaterialFile('one', '说明书／补充.docx')],
                              [requirement(e)], '匿名系统')
    assert result.complete and result.rows[0].file_ids == ('one',)


def test_keyword_does_not_match_extension_or_parent_directory():
    e = engine()
    assert not e.match_materials([e.MaterialFile('one', '报告.docx')],
                                [requirement(e, keywords='docx')], '匿名系统').complete
    result = e.check_snapshot(snapshot(document(name='代码.docx',
                              relative_path='说明书目录/代码.docx')),
                              [requirement(e)], '匿名系统')
    assert not result.complete and result.rows[0].matched == 0


@pytest.mark.parametrize('token', ['软件名称', '当前软件名', '{software}', '{软件名称}'])
def test_project_placeholder_rejects_another_project(token):
    e = engine()
    rules = [requirement(e, keywords=token + '，说明书')]
    assert e.match_materials([e.MaterialFile('one', 'ＡＰＩ_说明书_本－系统.docx')],
                            rules, '本系统').complete
    assert not e.match_materials([e.MaterialFile('one', '别的系统_说明书.docx')],
                                rules, '本系统').complete


@pytest.mark.parametrize('keywords', ['___', '－－－', '＿—·', '说明书，___'])
def test_ignored_separator_only_terms_are_not_hidden_wildcards(keywords):
    e = engine()
    with pytest.raises(ValueError):
        e.match_materials([], [requirement(e, keywords=keywords)], '匿名系统')


def test_literal_marker_and_label_are_not_hidden_patterns():
    e = engine()
    rule = requirement(e, keywords='说明书(V1)')
    assert e.match_materials([e.MaterialFile('one', '说明书(V1).docx')], [rule], '匿名系统').complete
    assert not e.match_materials([e.MaterialFile('one', '说明书V1.docx')], [rule], '匿名系统').complete


def test_counts_are_invariant_under_input_order_and_duplicate_rows():
    e = engine()
    a, b, c = [e.MaterialFile(k, n) for k, n in
               [('a', '第一份说明书.doc'), ('b', '第二份说明书.docx'), ('c', '代码.pdf')]]
    rules = [requirement(e, count=2), requirement(e, 'code', 'pdf', keywords='代码')]
    reports = [e.match_materials(fs, rs, '匿名系统')
               for fs, rs in [([a, b, c, a], rules), ([c, b, a, b], list(reversed(rules)))]]
    for report in reports:
        assert report.complete and sum(row.matched for row in report.rows) == 3
    assert sorted((r.matched, r.missing, tuple(sorted(r.file_ids))) for r in reports[0].rows) == sorted(
        (r.matched, r.missing, tuple(sorted(r.file_ids))) for r in reports[1].rows)


def test_distinct_file_ids_are_distinct_metadata_not_content_deduplication():
    e = engine()
    files = [e.MaterialFile('folder-a', '说明书.docx'), e.MaterialFile('folder-b', '说明书.docx')]
    result = e.match_materials(files, [requirement(e, count=2)], '匿名系统')
    assert result.complete and result.rows[0].matched == 2


def test_ambiguity_remains_explicit_even_when_other_files_fill_requirements():
    e = engine()
    files = [e.MaterialFile('a', '甲.docx'), e.MaterialFile('b', '乙.docx'),
             e.MaterialFile('ambiguous', '甲乙.docx')]
    rules = [requirement(e, 'a', keywords='甲'), requirement(e, 'b', keywords='乙')]
    result = e.match_materials(files, rules, '匿名系统')
    assert not result.complete and result.ambiguous == {'ambiguous': ('a', 'b')}
    selected = e.match_materials(files, rules, '匿名系统', selections={'ambiguous': 'a'})
    assert selected.complete and sum(row.matched for row in selected.rows) == 2


def test_choice_cannot_survive_file_rename_to_nonmatching_name():
    e = engine()
    with pytest.raises(ValueError):
        e.match_materials([e.MaterialFile('one', '不相关.pdf')], [requirement(e)],
                          '匿名系统', selections={'one': 'manual'})


@pytest.mark.parametrize('changes', [dict(kind='ZIP归档'), dict(kind='材料归档'),
                                    dict(exists=False), dict(is_directory=True)])
def test_noncurrent_file_cannot_complete_required_material(changes):
    e = engine()
    result = e.check_snapshot(snapshot(document(**changes)), [requirement(e)], '匿名系统')
    assert not result.complete and result.rows[0].matched == 0 and result.ignored == 1


@pytest.mark.parametrize('changes', [dict(errors=['已截断，只显示部分文件']),
                                    dict(workspace_path=''), dict(workspace_path=None)])
def test_matching_rows_do_not_hide_incomplete_scan(changes):
    e = engine()
    result = e.check_snapshot(snapshot(document(), **changes), [requirement(e)], '匿名系统')
    assert not result.complete and result.status == 'incomplete' and result.warnings


def test_matching_is_metadata_only_and_does_not_modify_inputs(monkeypatch):
    e = engine()
    rules = [requirement(e)]
    data = snapshot(document())
    before = copy.deepcopy(data)
    encoded = copy.deepcopy(e.encode_rules(rules))
    def prohibited(*args, **kwargs):
        pytest.fail('Pure material matching must not access file contents or filesystem state')
    with monkeypatch.context() as guard:
        guard.setattr('builtins.open', prohibited)
        guard.setattr(Path, 'stat', prohibited)
        result = e.check_snapshot(data, rules, '匿名系统')
    assert result.complete and data == before and e.encode_rules(rules) == encoded


@pytest.mark.parametrize('schema', [True, 1.0, '1', None, 2])
def test_saved_schema_version_is_an_integer_not_a_truthy_alias(schema):
    e = engine()
    value = e.encode_rules([requirement(e)])
    value['schema'] = schema
    with pytest.raises(ValueError):
        e.decode_rules(value)


def test_saved_duplicate_ids_cannot_change_counts_or_silently_replace_rules():
    e = engine()
    value = e.encode_rules([requirement(e)])
    value['rules'].append(copy.deepcopy(value['rules'][0]))
    with pytest.raises(ValueError):
        e.decode_rules(value)


def store_module():
    engine()
    assert (Path(__file__).resolve().parents[1] / 'material_check_store.py').is_file(), (
        'project-scoped material settings are not present in this source commit')
    return importlib.import_module('material_check_store')


@pytest.fixture
def anonymous_service(tmp_path):
    from desktop_service import DesktopService
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    service.receive_text('1. 验收甲系统\n2. 验收乙系统')
    workspace = Path(service.workspaces.get_workspace(1)['path'])
    (workspace / '交付样本.txt').write_bytes(b'Anonymous original delivery fixture')
    service.confirm_delivery(1, confirmed=True)
    return service


def database_snapshot(service, *, include_settings=True):
    """Compare all anonymous logical records and schema, not SQLite page layout."""
    with service.db.connect() as db:
        schema = tuple(tuple(row) for row in db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        values = {}
        for table in tables:
            if table == 'desktop_settings' and not include_settings:
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            values[table] = tuple(sorted((tuple(row) for row in db.execute(
                'SELECT * FROM ' + quoted)), key=repr))
    return schema, values


def test_opening_material_rules_does_not_create_settings_or_alter_history(anonymous_service):
    store = store_module()
    before = database_snapshot(anonymous_service)
    assert store.load_rules(anonymous_service, 1) is None
    assert store.load_rules(anonymous_service, 2) is None
    assert database_snapshot(anonymous_service) == before


def test_explicit_rule_save_changes_only_its_project_settings(anonymous_service):
    e, store = engine(), store_module()
    path = Path(anonymous_service.workspaces.get_workspace(1)['path']) / '说明书.docx'
    path.write_bytes(b'Anonymous original file, never modified by material rules')
    original = path.read_bytes()
    before = database_snapshot(anonymous_service, include_settings=False)
    rules = (requirement(e),)
    store.save_rules(anonymous_service, 1, rules)
    assert store.load_rules(anonymous_service, 1) == rules
    assert store.load_rules(anonymous_service, 2) is None
    assert database_snapshot(anonymous_service, include_settings=False) == before
    assert path.read_bytes() == original
    with anonymous_service.db.connect() as db:
        assert [row[0] for row in db.execute(
            "SELECT key FROM desktop_settings WHERE key LIKE 'material_check_v1:%'")] == [store.setting_key(1)]


def test_invalid_rule_save_preserves_previous_valid_settings(anonymous_service):
    e, store = engine(), store_module()
    store.save_rules(anonymous_service, 1, (requirement(e),))
    before = database_snapshot(anonymous_service)
    with pytest.raises(ValueError):
        store.save_rules(anonymous_service, 1, (requirement(e, count=True),))
    assert database_snapshot(anonymous_service) == before


def test_corrupt_persisted_rules_are_not_silently_replaced(anonymous_service):
    store = store_module()
    with anonymous_service.db.connect() as db:
        db.execute('INSERT INTO desktop_settings(key,value) VALUES (?,?)',
                   (store.setting_key(1), '{invalid'))
    before = database_snapshot(anonymous_service)
    with pytest.raises(ValueError):
        store.load_rules(anonymous_service, 1)
    assert database_snapshot(anonymous_service) == before


def test_deleted_project_rules_remain_available_after_explicit_restore(anonymous_service):
    e, store = engine(), store_module()
    rules = (requirement(e),)
    store.save_rules(anonymous_service, 1, rules)
    anonymous_service.db.delete_project(1, confirmed=True)
    before = database_snapshot(anonymous_service)
    with pytest.raises(ValueError):
        store.load_rules(anonymous_service, 1)
    assert database_snapshot(anonymous_service) == before
    anonymous_service.db.restore_deleted_project(1)
    assert store.load_rules(anonymous_service, 1) == rules


def test_project_deleted_between_validation_and_write_cannot_gain_settings(anonymous_service, monkeypatch):
    e, store = engine(), store_module()
    original = store.check_project
    def concurrent_delete(service, project_id):
        project = original(service, project_id)
        service.db.delete_project(project_id, confirmed=True)
        return project
    monkeypatch.setattr(store, 'check_project', concurrent_delete)
    with pytest.raises(ValueError):
        store.save_rules(anonymous_service, 1, (requirement(e),))
    assert anonymous_service.get_setting(store.setting_key(1)) is None


def test_merged_source_cannot_save_or_silently_transfer_rules(anonymous_service):
    e, store = engine(), store_module()
    store.save_rules(anonymous_service, 1, (requirement(e),))
    # A valid anonymous merge identity marker; actual file merging is not this test's task.
    with anonymous_service.db.connect() as db:
        db.execute('INSERT INTO project_merges(source_id,target_id,operation_id,snapshot_json) VALUES (?,?,?,?)',
                   (1, 2, 'anonymous-material-acceptance-merge', '{}'))
    before = database_snapshot(anonymous_service)
    with pytest.raises(ValueError):
        store.save_rules(anonymous_service, 1, (requirement(e, keywords='新版'),))
    with pytest.raises(ValueError):
        store.load_rules(anonymous_service, 1)
    assert store.load_rules(anonymous_service, 2) is None
    assert database_snapshot(anonymous_service) == before
