"""登记旧名的精确收件匹配，不读取真实项目数据库。"""
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from bundle_analyzer import analyze_bundle
from file_matcher import match_work_file_projects
from folder_intake import FolderIntake
from zip_content import count_project_files
from zip_manager import match_projects


def project(pid, name, aliases=(), batch=1, **extra):
    return dict(id=pid, name=name, name_aliases=list(aliases), batch_id=batch,
                batch_name='隔离批次', archived_at=None, deleted_at=None, **extra)


def package(tmp_path, name='旧甲系统-2.zip', entries=None):
    path = tmp_path / name
    with ZipFile(path, 'w') as archive:
        for entry in entries or ['旧甲系统/a.txt', '旧乙系统/b.txt']:
            archive.writestr(entry, b'isolated')
    return path


@pytest.mark.parametrize('filename', ['旧甲系统.docx', '旧甲系统_申请表重新下载_v1.0 (1).docx'])
def test_ordinary_material_uses_registered_alias_without_changing_project_name(filename):
    row = project(1, '甲系统', ['旧甲系统'])
    result = match_work_file_projects(filename, [row])
    assert result['candidates'] == [row] and result['candidates'][0] is row
    assert result['candidates'][0]['name'] == '甲系统'


@pytest.mark.parametrize('matcher,filename', [
    (match_work_file_projects, '旧甲系统_申请表.docx'),
    (match_projects, '旧甲系统_v1.0.zip'),
])
def test_complete_current_name_flow_wins_over_more_literal_alias(matcher, filename):
    current = project(1, '旧甲系统')
    historical = project(2, '其他项目', [Path(filename).stem])
    result = matcher(filename, [historical, current])
    assert (result['candidates'] if isinstance(result, dict) else result) == [current]


@pytest.mark.parametrize('matcher,filename', [(match_work_file_projects, '旧甲系统_代码（C#）.txt'),
                                             (match_projects, '旧甲系统_交付_v1.0.zip')])
def test_alias_collisions_keep_every_candidate(matcher, filename):
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧甲系统'])]
    result = matcher(filename, rows)
    assert (result['candidates'] if isinstance(result, dict) else result) == rows


def test_zip_alias_and_fullwidth_normalization_preserve_canonical_record():
    row = project(1, '甲系统', ['My Old Project'])
    assert match_projects('Ｍｙ Ｏｌｄ Ｐｒｏｊｅｃｔ_最终版.zip', [row]) == [row]


def test_unregistered_or_partial_old_names_do_not_match():
    row = project(1, '甲系统', ['旧甲系统'])
    assert match_work_file_projects('旧甲_代码.txt', [row])['candidates'] == []
    assert match_projects('旧甲.zip', [row]) == []
    assert match_projects('旧甲系统.zip', [dict(id=2, name='无登记')]) == []


def test_bundle_old_directories_return_current_names_and_support_recount(tmp_path):
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统'])]
    path = package(tmp_path)
    plan = analyze_bundle(path, rows)
    assert plan['state'] == 'matched' and plan['project_ids'] == [1, 2]
    assert [match['project_name'] for match in plan['matches']] == ['甲系统', '乙系统']
    assert [match['folder_name'] for match in plan['matches']] == ['旧甲系统', '旧乙系统']
    assert count_project_files(path, rows, [1, 2], bundle_plan=plan) == {1: 1, 2: 1}
    assert count_project_files(path, rows, [1, 2]) == {1: 1, 2: 1}


def test_bundle_current_name_takes_priority_over_another_projects_alias(tmp_path):
    rows = [project(1, '旧甲系统'), project(2, '乙系统', ['旧乙系统']), project(3, '别的项目', ['旧甲系统'])]
    path = package(tmp_path)
    plan = analyze_bundle(path, rows)
    assert plan['state'] == 'matched' and plan['project_ids'] == [1, 2]
    assert count_project_files(path, rows, [1, 2], bundle_plan=plan) == {1: 1, 2: 1}


def test_bundle_alias_ambiguity_blocks_auto_creation_and_counting(tmp_path):
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统']),
            project(3, '另一甲系统', ['旧甲系统'], batch=2)]
    path = package(tmp_path)
    plan = analyze_bundle(path, rows)
    assert plan['state'] == 'needs-review' and plan['project_ids'] == []
    assert plan['provision_names'] == []
    with pytest.raises(ValueError):
        count_project_files(path, rows, [1, 2])


def test_full_registered_alias_name_prevents_bundle_misclassification(tmp_path):
    rows = [project(1, '新名称', ['旧甲系统-2'])]
    path = package(tmp_path)
    assert analyze_bundle(path, rows)['state'] == 'not-bundle'


def test_folder_root_and_child_alias_match_canonical_project_and_keep_conflicts():
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统'])]
    service = SimpleNamespace(workspaces=None, list_projects=lambda **kwargs: rows,
        file_routing=lambda path: match_work_file_projects(str(path), rows), is_bundle_candidate=lambda name: False)
    intake = FolderIntake(service)
    assert intake._named('旧甲系统', rows) == [rows[0]]
    root = Path('D:/isolated/旧甲系统')
    plans = intake._plan(root, [root / '资料.txt'], None, None)
    assert plans[0]['project_id'] == 1 and not plans[0]['routing']['requires_choice']
    grouped_root = Path('D:/isolated/group')
    plans = intake._plan(grouped_root, [grouped_root / '旧甲系统' / '资料.txt', grouped_root / '旧乙系统' / '说明.txt'], None, None)
    assert [row['project_id'] for row in plans] == [1, 2]
    assert [str(row['relative_path']) for row in plans] == ['资料.txt', '说明.txt']
    rows.append(project(3, '丙系统', ['旧甲系统']))
    route = intake._directory_route(['旧甲系统'], rows)
    assert route['requires_choice'] and {row['id'] for row in route['candidates']} == {1, 3}


def test_folder_current_name_wins_over_other_alias():
    rows = [project(1, '旧甲系统'), project(2, '乙系统', ['旧甲系统'])]
    intake = FolderIntake(SimpleNamespace(workspaces=None))
    assert intake._named('旧甲系统', rows) == [rows[0]]


@pytest.mark.parametrize('boundary', ['archived_at', 'deleted_at'])
def test_alias_protected_project_boundary_does_not_disappear_in_bundle_or_folder(tmp_path, boundary):
    protected = project(3, '保留旧项目', ['旧甲系统'])
    protected[boundary] = '2026-10-01'
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统']), protected]
    plan = analyze_bundle(package(tmp_path), rows)
    assert plan['state'] == 'needs-review' and plan['provision_names'] == []
    intake = FolderIntake(SimpleNamespace(workspaces=None))
    assert intake._directory_route(['旧甲系统'], rows)['requires_choice']


def test_current_archived_full_name_cannot_be_bypassed_by_an_active_alias(tmp_path):
    protected = project(3, '旧甲系统')
    protected['archived_at'] = '2026-10-01'
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统']), protected]
    assert match_projects('旧甲系统.zip', rows) == [protected]
    assert match_work_file_projects('旧甲系统_申请表.docx', rows)['candidates'] == [protected]
    assert analyze_bundle(package(tmp_path), rows)['state'] == 'needs-review'


def test_recount_rechecks_registered_alias_and_current_name_precedence(tmp_path):
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统'])]
    path = package(tmp_path)
    plan = analyze_bundle(path, rows)
    changed = [project(1, '甲系统'), rows[1]]
    with pytest.raises(ValueError):
        count_project_files(path, changed, [1, 2], bundle_plan=plan)
    changed = [*rows, project(3, '旧甲系统')]
    with pytest.raises(ValueError):
        count_project_files(path, changed, [1, 2], bundle_plan=plan)


def test_single_project_recount_excludes_other_project_alias_subdirectory(tmp_path):
    rows = [project(1, '甲系统', ['旧甲系统']), project(2, '乙系统', ['旧乙系统'])]
    path = package(tmp_path, '旧甲系统.zip', ['旧甲系统/a.txt', '旧甲系统/旧乙系统/b.txt'])
    assert count_project_files(path, rows, [1]) == {1: 1}
