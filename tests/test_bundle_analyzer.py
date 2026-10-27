import importlib
import importlib.util
from zipfile import ZipFile

import pytest


FIRST = '隔离测试项目6系统'
SECOND = '隔离测试项目7系统'


def analyzer():
    assert importlib.util.find_spec('bundle_analyzer') is not None, 'bundle analyzer is missing'
    return importlib.import_module('bundle_analyzer')


def project(pid, name, batch=1, archived=None):
    return dict(id=pid, name=name, batch_id=batch, archived_at=archived)


def projects():
    return [project(11, FIRST), project(12, SECOND)]


def package(tmp_path, entries=None, filename=None):
    path = tmp_path / (filename or f'{FIRST}-2-G.zip')
    with ZipFile(path, 'w') as archive:
        for entry in entries or [f'{FIRST}/材料.docx', f'{SECOND}/说明.pdf']:
            archive.writestr(entry, b'isolated fixture')
    return path


@pytest.mark.parametrize('name,count', [(f'{FIRST}-2-G.zip', 2), (f'{FIRST}-2.zip', 2),
                                       (f'{FIRST}－２－Ｇ.ＺＩＰ', 2), ('A-500-G.zip', 500), ('A-1.zip', 1)])
def test_hint_recognizes_only_explicit_final_count(name, count):
    value = analyzer().bundle_hint(name)
    assert value == dict(head_name=FIRST if count == 2 else 'A', declared_count=count)


@pytest.mark.parametrize('name', ['A.zip', 'A-0-G.zip', 'A-501.zip', 'A-2-more.zip',
                                  'A-2G.zip', 'A–2-G.zip', 'A-2-G.txt', '-2.zip', 'A-02.zip'])
def test_non_hints_are_not_guessed(name):
    assert analyzer().bundle_hint(name) is None


def test_two_members_are_matched_and_original_filename_is_respected(tmp_path):
    path = package(tmp_path, filename='physically-renamed.zip')
    value = analyzer().analyze_bundle(path, projects(), filename=f'{FIRST}-2-G.zip')
    assert value['state'] == 'matched'
    assert value['is_bundle'] is True
    assert value['declared_count'] == 2
    assert value['member_names'] == [FIRST, SECOND]
    assert value['project_ids'] == [11, 12]
    assert value['matches'] == [dict(folder_name=FIRST, folder_path=FIRST, file_count=1,
                                    project_id=11, project_name=FIRST, batch_id=1),
                                dict(folder_name=SECOND, folder_path=SECOND, file_count=1,
                                     project_id=12, project_name=SECOND, batch_id=1)]


@pytest.mark.parametrize('depth', [1, 2, 4])
def test_common_wrapper_directories_are_allowed(tmp_path, depth):
    prefix = '/'.join([FIRST] * depth)
    path = package(tmp_path, [f'{prefix}/{FIRST}/a.txt', f'{prefix}/{SECOND}/b.txt', 'README.txt'])
    assert analyzer().analyze_bundle(path, projects())['state'] == 'matched'


def test_count_without_g_has_same_meaning(tmp_path):
    path = package(tmp_path, filename=f'{FIRST}-2.zip')
    assert analyzer().analyze_bundle(path, projects())['project_ids'] == [11, 12]


@pytest.mark.parametrize('entries', [[f'{FIRST}/a.txt'], [f'{FIRST}/a.txt', f'{SECOND}/b.txt', 'other/c.txt'],
                                    [f'{FIRST}/a.txt', 'unknown/b.txt'],
                                    [f'x/x/x/x/x/{FIRST}/a.txt', f'x/x/x/x/x/{SECOND}/b.txt']])
def test_incomplete_or_extra_members_never_return_partial_project_ids(tmp_path, entries):
    value = analyzer().analyze_bundle(package(tmp_path, entries), projects())
    assert value['state'] == 'needs-review'
    assert value['project_ids'] == []
    assert value['matches'] == []
    assert value['reason']


def test_duplicate_names_require_unique_complete_batch(tmp_path):
    path = package(tmp_path)
    rows = [*projects(), project(21, FIRST, 2)]
    assert analyzer().analyze_bundle(path, rows)['project_ids'] == [11, 12]
    rows.append(project(22, SECOND, 2))
    value = analyzer().analyze_bundle(path, rows)
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_duplicate_name_inside_same_batch_still_requires_review(tmp_path):
    rows = [*projects(), project(13, FIRST)]
    assert analyzer().analyze_bundle(package(tmp_path), rows)['state'] == 'needs-review'


def test_unique_names_from_different_batches_cannot_be_joined(tmp_path):
    rows = [project(11, FIRST, 1), project(12, SECOND, 2)]
    value = analyzer().analyze_bundle(package(tmp_path), rows)
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_g_suffix_does_not_match_a_project_named_g(tmp_path):
    rows = [*projects(), project(99, 'G')]
    value = analyzer().analyze_bundle(package(tmp_path), rows)
    assert value['project_ids'] == [11, 12]


def test_archived_project_is_not_automatically_matched(tmp_path):
    rows = [project(11, FIRST), project(12, SECOND, archived='2026-01-01')]
    assert analyzer().analyze_bundle(package(tmp_path), rows)['project_ids'] == []


def test_exact_full_project_name_takes_priority_over_count_suffix(tmp_path):
    path = package(tmp_path)
    rows = [*projects(), project(31, f'{FIRST}-2-G')]
    value = analyzer().analyze_bundle(path, rows)
    assert value['state'] == 'not-bundle' and value['is_bundle'] is False
    assert value['project_ids'] == []


def test_no_hint_does_not_open_archive(tmp_path, monkeypatch):
    module = analyzer()
    monkeypatch.setattr(module, 'ZipLease', lambda *a, **k: pytest.fail('not a bundle'))
    assert module.analyze_bundle(tmp_path / 'ordinary.zip', projects())['state'] == 'not-bundle'


def test_analysis_reads_only_central_directory_and_does_not_extract(tmp_path, monkeypatch):
    module = analyzer()
    path = package(tmp_path)
    before = path.read_bytes()
    for method in ('open', 'read', 'extract', 'extractall', 'testzip'):
        monkeypatch.setattr(ZipFile, method, lambda *a, **k: pytest.fail('content must not be read'))
    assert module.analyze_bundle(path, projects())['state'] == 'matched'
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_metadata_and_root_readme_do_not_add_members(tmp_path):
    entries = [f'{FIRST}/a.txt', f'{SECOND}/b.txt', '__MACOSX/._a', '.DS_Store',
               f'{FIRST}/.DS_Store', f'__MACOSX/{SECOND}/._b', 'README.txt']
    assert analyzer().analyze_bundle(package(tmp_path, entries), projects())['state'] == 'matched'


def test_metadata_files_cannot_make_an_empty_project_nonempty(tmp_path):
    entries = [f'{FIRST}/a.txt', f'{SECOND}/.DS_Store', f'{SECOND}/__MACOSX/._fake']
    assert analyzer().analyze_bundle(package(tmp_path, entries), projects())['state'] == 'needs-review'


def test_empty_zip_is_reviewable_without_a_partial_match(tmp_path):
    path = tmp_path / f'{FIRST}-2-G.zip'
    with ZipFile(path, 'w'):
        pass
    value = analyzer().analyze_bundle(path, projects())
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_normalized_exact_names_match_without_substring_guessing(tmp_path):
    rows = [project(1, 'Ａ  B'), project(2, '另一个')]
    path = package(tmp_path, ['a B/a.txt', '另一个/b.txt'], filename='Ａ B-2-G.zip')
    assert analyzer().analyze_bundle(path, rows)['project_ids'] == [1, 2]
    rows[0]['name'] = 'a B 延伸版'
    assert analyzer().analyze_bundle(path, rows)['project_ids'] == []


def test_head_name_must_be_one_of_members(tmp_path):
    path = package(tmp_path, filename='other-2-G.zip')
    assert analyzer().analyze_bundle(path, projects())['state'] == 'needs-review'


@pytest.mark.parametrize('entry', ['../escape.txt', '/absolute/file.txt', 'C:/absolute/file.txt',
                                   'safe/../../escape.txt', '\\server\\share\\a.txt', 'safe/..\\escape.txt'])
def test_unsafe_entry_paths_reject_entire_bundle(tmp_path, entry):
    path = package(tmp_path, [f'{FIRST}/a.txt', f'{SECOND}/b.txt', entry])
    value = analyzer().analyze_bundle(path, projects())
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_nul_in_central_directory_name_is_rejected(tmp_path):
    path = package(tmp_path, [f'{FIRST}/a.txt', f'{SECOND}/b.txt', 'evilXname.txt'])
    payload = path.read_bytes().replace(b'evilXname.txt', b'evil\x00name.txt')
    path.write_bytes(payload)
    assert analyzer().analyze_bundle(path, projects())['state'] == 'needs-review'


def test_duplicate_normalized_member_names_are_ambiguous(tmp_path):
    path = package(tmp_path, ['Ａ/a.txt', 'A/b.txt'], filename='A-2-G.zip')
    value = analyzer().analyze_bundle(path, [project(1, 'A')])
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_member_limit_rejects_archive_before_matching(tmp_path):
    entries = [f'{FIRST}/{n}.txt' for n in range(10000)] + [f'{SECOND}/b.txt']
    value = analyzer().analyze_bundle(package(tmp_path, entries), projects())
    assert value['state'] == 'needs-review' and value['project_ids'] == []


def test_invalid_zip_is_clear_value_error(tmp_path):
    path = tmp_path / f'{FIRST}-2-G.zip'
    path.write_bytes(b'not a zip')
    with pytest.raises(ValueError):
        analyzer().analyze_bundle(path, projects())
