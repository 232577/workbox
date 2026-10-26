import importlib
import importlib.util
import stat
from zipfile import ZipFile, ZipInfo

import pytest


PROJECTS = [dict(id=1, name='甲系统', batch_id=8, archived_at=None),
            dict(id=2, name='乙系统', batch_id=8, archived_at=None)]


def counter():
    assert importlib.util.find_spec('zip_content') is not None, 'ZIP project ownership counter is missing'
    return importlib.import_module('zip_content').count_project_files


def package(tmp_path, names, filename='甲系统.zip'):
    path = tmp_path / filename
    with ZipFile(path, 'w') as archive:
        for name in names:
            archive.writestr(name, b'')
    return path


def test_single_flat_zip_counts_zero_byte_files_but_not_directories(tmp_path):
    path = package(tmp_path, ['a.txt', 'b.txt', 'c.txt', 'd.txt', 'empty/'])
    assert counter()(path, PROJECTS, [1]) == {1: 4}


def test_single_project_counts_nested_files_recursively(tmp_path):
    path = package(tmp_path, ['甲系统/a.txt', '甲系统/docs/b.txt',
                             '甲系统/docs/deep/c.txt', '甲系统/empty/'])
    assert counter()(path, PROJECTS, [1]) == {1: 3}


def test_multi_project_zip_uses_each_projects_own_count(tmp_path):
    path = package(tmp_path, ['outer/甲系统/a.txt', 'outer/乙系统/a.txt',
                             'outer/乙系统/b.txt', 'outer/乙系统/deep/c.txt',
                             'outer/乙系统/deep/d.txt', 'README.txt'])
    assert counter()(path, PROJECTS, [1, 2]) == {1: 1, 2: 4}


def test_selected_project_does_not_inherit_other_known_project_files(tmp_path):
    path = package(tmp_path, ['甲系统/a.txt', '甲系统/乙系统/a.txt',
                             '甲系统/乙系统/b.txt', '甲系统/乙系统/c.txt',
                             '甲系统/乙系统/d.txt'])
    assert counter()(path, PROJECTS, [1]) == {1: 1}


def test_empty_project_directory_has_zero_files(tmp_path):
    path = package(tmp_path, ['甲系统/', '乙系统/a.txt'])
    assert counter()(path, PROJECTS, [1, 2]) == {1: 0, 2: 1}


def test_empty_single_archive_has_zero_files(tmp_path):
    assert counter()(package(tmp_path, []), PROJECTS, [1]) == {1: 0}


def test_full_name_wins_over_substring(tmp_path):
    rows = [*PROJECTS, dict(id=3, name='甲系统扩展版')]
    path = package(tmp_path, ['甲系统扩展版/a.txt', '甲系统扩展版/b.txt'])
    with pytest.raises(ValueError):
        counter()(path, rows, [1])
    assert counter()(path, rows, [3]) == {3: 2}


def test_confirmed_single_project_generic_directories_are_counted(tmp_path):
    path = package(tmp_path, ['src/a.py', 'src/deep/b.py', 'docs/c.txt', 'docs/d.txt'])
    assert counter()(path, PROJECTS, [1]) == {1: 4}


@pytest.mark.parametrize('names,ids', [(['甲系统/a.txt'], [1, 2]),
                                      (['x/甲系统/a.txt', 'y/甲系统/b.txt'], [1]),
                                      (['a.txt'], [1, 2])])
def test_unresolved_ownership_is_rejected(tmp_path, names, ids):
    with pytest.raises(ValueError):
        counter()(package(tmp_path, names), PROJECTS, ids)


def test_confirmed_single_project_id_resolves_same_name_in_other_batches(tmp_path):
    path = package(tmp_path, ['甲系统/a.txt'])
    rows = [*PROJECTS, dict(id=3, name='甲系统', batch_id=9)]
    assert counter()(path, rows, [1]) == {1: 1}


def test_multiple_selected_project_ids_cannot_share_one_named_directory(tmp_path):
    path = package(tmp_path, ['甲系统/a.txt'])
    rows = [*PROJECTS, dict(id=3, name='甲系统', batch_id=9)]
    with pytest.raises(ValueError):
        counter()(path, rows, [1, 3])


def test_matched_bundle_retains_full_wrapper_paths_and_project_counts(tmp_path):
    from bundle_analyzer import analyze_bundle
    path = package(tmp_path, ['甲系统/wrapper/甲系统/a.txt',
                             '甲系统/wrapper/乙系统/a.txt',
                             '甲系统/wrapper/乙系统/deep/b.txt'], '甲系统-2-G.zip')
    plan = analyze_bundle(path, PROJECTS)
    assert plan['state'] == 'matched'
    assert plan['matches'][0].get('folder_path') == '甲系统/wrapper/甲系统'
    assert plan['matches'][1].get('file_count') == 2
    assert counter()(path, PROJECTS, [1, 2], bundle_plan=plan) == {1: 1, 2: 2}


def test_bundle_plan_cannot_reuse_outdated_counts(tmp_path):
    from bundle_analyzer import analyze_bundle
    path = package(tmp_path, ['甲系统/a.txt', '乙系统/a.txt'], '甲系统-2.zip')
    plan = analyze_bundle(path, PROJECTS)
    plan['matches'][0]['file_count'] = 100
    assert counter()(path, PROJECTS, [1, 2], bundle_plan=plan) == {1: 1, 2: 1}


@pytest.mark.parametrize('entry', ['../escape', '/abs/file', 'C:/file', 'safe/..\\escape',
                                  'safe//file'])
def test_unsafe_paths_reject_entire_zip(tmp_path, entry):
    with pytest.raises(ValueError):
        counter()(package(tmp_path, ['a.txt', entry]), PROJECTS, [1])


def test_symlink_members_are_rejected(tmp_path):
    link = ZipInfo('link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError):
        counter()(package(tmp_path, ['a.txt', link]), PROJECTS, [1])


@pytest.mark.parametrize('platform,attributes', [(3, (stat.S_IFDIR | 0o755) << 16), (0, 0x10)])
def test_directory_attributes_without_trailing_slash_do_not_count(tmp_path, platform, attributes):
    directory = ZipInfo('folder')
    directory.create_system = platform
    directory.external_attr = attributes
    assert counter()(package(tmp_path, ['a.txt', directory]), PROJECTS, [1]) == {1: 1}


def test_special_members_are_rejected(tmp_path):
    pipe = ZipInfo('pipe')
    pipe.create_system = 3
    pipe.external_attr = (stat.S_IFIFO | 0o600) << 16
    with pytest.raises(ValueError):
        counter()(package(tmp_path, ['a.txt', pipe]), PROJECTS, [1])


@pytest.mark.parametrize('names', [['a', 'a/b.txt'], ['a/b.txt', 'a'],
                                  ['root/Ａ/a.txt', 'root/A/b.txt']])
def test_file_directory_conflicts_and_aliased_directories_are_rejected(tmp_path, names):
    with pytest.raises(ValueError):
        counter()(package(tmp_path, names), PROJECTS, [1])


def test_nul_in_central_directory_name_is_rejected(tmp_path):
    path = package(tmp_path, ['evilXname.txt'])
    path.write_bytes(path.read_bytes().replace(b'evilXname.txt', b'evil\x00name.txt'))
    with pytest.raises(ValueError):
        counter()(path, PROJECTS, [1])


def test_deep_directories_do_not_depend_on_python_recursion(tmp_path):
    path = package(tmp_path, ['甲系统/' + 'nested/' * 1100 + 'a.txt'])
    assert counter()(path, PROJECTS, [1]) == {1: 1}


def test_duplicate_members_are_rejected_instead_of_double_counted(tmp_path):
    with pytest.warns(UserWarning):
        path = package(tmp_path, ['a.txt', 'a.txt'])
    with pytest.raises(ValueError):
        counter()(path, PROJECTS, [1])


def test_normalized_duplicate_members_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        counter()(package(tmp_path, ['Ａ.txt', 'A.txt']), PROJECTS, [1])


def test_metadata_does_not_inflate_file_counts(tmp_path):
    path = package(tmp_path, ['a.txt', '.DS_Store', '__MACOSX/._a'])
    assert counter()(path, PROJECTS, [1]) == {1: 1}


def test_member_limit_is_explicitly_rejected(tmp_path):
    path = package(tmp_path, [f'{n}.txt' for n in range(10001)])
    with pytest.raises(ValueError, match='10000'):
        counter()(path, PROJECTS, [1])


def test_counter_reads_only_central_directory_without_touching_payload(tmp_path, monkeypatch):
    path = package(tmp_path, ['a.txt', 'b.txt'])
    for method in ('open', 'read', 'extract', 'extractall', 'testzip'):
        monkeypatch.setattr(ZipFile, method, lambda *a, **k: pytest.fail('payload must not be read'))
    assert counter()(path, PROJECTS, [1]) == {1: 2}


def test_invalid_zip_has_clear_value_error(tmp_path):
    path = tmp_path / '甲系统.zip'
    path.write_bytes(b'not a zip')
    with pytest.raises(ValueError):
        counter()(path, PROJECTS, [1])
