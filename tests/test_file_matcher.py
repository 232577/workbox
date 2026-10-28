"""普通工作文件归属只使用完整项目名和明确尾缀。"""

import pytest

from file_matcher import match_work_file_projects, new_file_project_name


@pytest.mark.parametrize(('filename', 'name'), [
    ('隔离测试项目4系统_代码（C#）.docx', '隔离测试项目4系统'),
    ('隔离测试项目5系统_代码（C#+Python）.docx', '隔离测试项目5系统'),
    ('隔离测试项目2系统_代码（C#+Python）.docx', '隔离测试项目2系统'),
    ('隔离测试项目3系统_代码（C++）.docx', '隔离测试项目3系统'),
    ('隔离测试项目7系统_代码(C++).docx', '隔离测试项目7系统'),
    ('隔离测试项目7系统_代码段_Cpp.docx', '隔离测试项目7系统'),
    ('隔离测试项目3系统 (1).txt', '隔离测试项目3系统'),
])
def test_reported_filenames_match_project_without_reading_documents(filename, name):
    project = {'id': 1, 'name': name}
    assert match_work_file_projects(filename, [project])['candidates'] == [project]


@pytest.mark.parametrize('filename', [
    '甲系统_代码（Python）.txt',
    '甲系统_源代码(C++).txt',
    '甲系统_代码段（C#）.txt',
    '甲系统_源程序(C#+Python).txt',
    '甲系统_代码（Java）.txt',
    '甲系统_代码(ASP.NET).txt',
    '甲系统_代码段_Cpp.txt',
    '甲系统_代码_CSharp.txt',
    '甲系统_代码.txt',
    '甲系统_操作手册.docx',
    '甲系统_用户手册.docx',
    '甲系统_申请书.docx',
    '甲系统_申请表.docx',
    '甲系统_代码（Python） (1).txt',
    '甲系统_代码（Python）_v1.0_交付_20261023.txt',
])
def test_explicit_work_file_suffix_matches_whole_project(filename):
    project = {'id': 1, 'name': '甲系统', 'batch_name': '本批'}
    result = match_work_file_projects(filename, [project])
    assert result['candidates'] == [project]
    assert result['candidates'][0] is project
    assert result['recognized_name'] == '甲系统'
    assert result['basis']


def test_complete_project_name_precedes_suffix_and_download_copy_rules():
    projects = [
        {'id': 1, 'name': '甲系统'},
        {'id': 2, 'name': '甲系统_代码（Python）'},
        {'id': 3, 'name': '甲系统_代码（Python） (1)'},
    ]
    assert match_work_file_projects('甲系统_代码（Python）.txt', projects)['candidates'] == [projects[1]]
    assert match_work_file_projects('甲系统_代码（Python） (1).txt', projects)['candidates'] == [projects[2]]


@pytest.mark.parametrize('filename', [
    '甲系统_未知.txt',
    '甲系统_代码（待审核）.txt',
    '甲系统代码(Python).txt',
    '甲系统操作手册.docx',
    '甲系统_代码（Python）(1).txt',
    '甲系统2_代码（Python）.txt',
    '甲_代码（Python）.txt',
    '甲系统_代码（Python）_草稿.txt',
    '甲系统_代码（Python）.zip',
])
def test_unknown_or_partial_names_do_not_guess_project(filename):
    result = match_work_file_projects(filename, [{'id': 1, 'name': '甲系统'}])
    assert result['candidates'] == []
    assert result['recognized_name'] is None


def test_duplicate_project_names_return_all_batches():
    projects = [
        {'id': 1, 'name': '甲系统', 'batch_name': '第一批'},
        {'id': 2, 'name': '甲系统', 'batch_name': '第二批'},
    ]
    assert match_work_file_projects('甲系统_代码（Python）.txt', projects)['candidates'] == projects


@pytest.mark.parametrize('name', ['A+C', 'AC', 'C++平台2', 'C#平台2', 'C平台2'])
def test_language_symbols_and_project_numbers_remain_distinct(name):
    projects = [{'id': index, 'name': value} for index, value in enumerate(
        ['A+C', 'AC', 'C++平台2', 'C#平台2', 'C平台2'], 1)]
    expected = [project for project in projects if project['name'] == name]
    assert match_work_file_projects(name + '_代码（C#+Python）.txt', projects)['candidates'] == expected


def test_normalization_supports_fullwidth_latin_and_consistent_whitespace():
    project = {'id': 1, 'name': 'My Project C#'}
    assert match_work_file_projects('Ｍｙ  Project C＃_代码（Java）.txt', [project])['candidates'] == [project]


def test_download_copy_does_not_remove_project_number_without_space():
    project = {'id': 1, 'name': '甲系统(1)'}
    assert match_work_file_projects('甲系统(1)_代码（Python） (2).txt', [project])['candidates'] == [project]


def test_full_name_with_version_takes_priority_over_shorter_project():
    projects = [{'id': 1, 'name': '甲系统'}, {'id': 2, 'name': '甲系统_v1.0'}]
    assert match_work_file_projects('甲系统_v1.0.txt', projects)['candidates'] == [projects[1]]
    assert match_work_file_projects('甲系统_v1.0_代码（Python）.txt', projects)['candidates'] == [projects[1]]


@pytest.mark.parametrize('suffix', [
    '_申请表重新下载', '_申请书_重新下载', '_操作手册 下载说明',
    '_用户手册-下载链接', '_申请表_下载链接_v1.0 (2)',
    '_申请表重新下载_最终版 (1)',
])
def test_explicit_material_download_suffix_matches_and_provisions_consistently(suffix):
    project = {'id': 1, 'name': '图像分析系统'}
    filename = project['name'] + suffix + '.txt'
    assert match_work_file_projects(filename, [project])['candidates'] == [project]
    assert new_file_project_name(filename) == project['name']


@pytest.mark.parametrize('suffix', [
    '_申请表重新下载备份', '_申请表下载', '_申请表任意内容下载链接',
    '_申请表下载链接地址', '_重新下载', '_下载说明', '申请表重新下载',
])
def test_material_download_suffix_does_not_strip_unknown_text(suffix):
    filename = '图像分析系统' + suffix + '.txt'
    assert match_work_file_projects(filename, [{'id': 1, 'name': '图像分析系统'}])['candidates'] == []
    assert new_file_project_name(filename) == '图像分析系统' + suffix


def test_material_complete_name_checked_after_each_copy_and_version_layer():
    projects = [{'id': 1, 'name': '图像分析系统'},
                {'id': 2, 'name': '图像分析系统_申请表重新下载'},
                {'id': 3, 'name': '图像分析系统_申请表重新下载_v1.0'}]
    for filename, expected in [
        ('图像分析系统_申请表重新下载.txt', projects[1]),
        ('图像分析系统_申请表重新下载 (1).txt', projects[1]),
        ('图像分析系统_申请表重新下载_v1.0 (2).txt', projects[2]),
    ]:
        assert match_work_file_projects(filename, projects)['candidates'] == [expected]
