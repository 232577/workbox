import importlib
import importlib.util
from pathlib import Path
from zipfile import ZipFile

import pytest
from parser import ParsedProject


def desktop(tmp_path):
    assert importlib.util.find_spec('desktop_service'), 'Floating desktop intake service missing'
    return importlib.import_module('desktop_service').DesktopService(tmp_path / 'data' / 'workbox.db')


def test_clear_dropped_message_creates_projects_without_a_form(tmp_path):
    service = desktop(tmp_path)
    raw = '1. 客户档案系统\n2. 合同归档工具，加急\n3. WorkBox'
    result = service.receive_text(raw)
    assert result['state'] == 'committed'
    assert service.db.counts() == {'projects': 3, 'batches': 1, 'zips': 0}
    assert service.get_project(2)['name'] == '合同归档工具'
    assert service.get_project(2)['urgent'] == 1
    assert service.get_project(2)['status'] == '待开发'
    assert service.get_project(2)['raw_text'] == raw


def test_repeated_drop_reuses_receipt_and_does_not_create_duplicate_projects(tmp_path):
    service = desktop(tmp_path)
    first = service.receive_text('1. 客户档案系统')
    second = service.receive_text('1. 客户档案系统')
    assert first['receipt_id'] == second['receipt_id']
    assert second['duplicate'] is True
    assert service.db.counts()['projects'] == 1


@pytest.mark.parametrize('text', ['你好，这里是说明，不是一份项目清单。', '客户档案系统.zip', 'https://example.invalid/file.zip', '1. 客户档案系统\n2个项目'])
def test_uncertain_or_filename_text_is_retained_for_review_not_auto_created(tmp_path, text):
    service = desktop(tmp_path)
    result = service.receive_text(text)
    assert result['state'] == 'needs-review'
    assert service.db.counts()['projects'] == 0
    reopened = desktop(tmp_path)
    assert reopened.pending_receipts()[0]['raw_text'] == text


def test_review_commits_edited_names_in_one_atomic_batch(tmp_path):
    service = desktop(tmp_path)
    result = service.receive_text('这里是描述性内容。')
    service.resolve_text(result['receipt_id'], [ParsedProject('人工指定项目', True)])
    assert service.pending_receipts() == []
    assert service.get_project(1)['name'] == '人工指定项目'
    assert service.get_project(1)['urgent'] == 1
    assert service.get_project(1)['raw_text'] == '这里是描述性内容。'


def test_failed_review_preserves_pending_text_without_partial_projects(tmp_path):
    service = desktop(tmp_path)
    result = service.receive_text('这里是描述性内容。')
    with pytest.raises(ValueError):
        service.resolve_text(result['receipt_id'], [ParsedProject('')])
    assert service.pending_receipts()
    assert service.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}


def test_settings_persist_but_do_not_seed_business_data(tmp_path):
    service = desktop(tmp_path)
    service.set_setting('orb_position', [12, 34])
    assert desktop(tmp_path).get_setting('orb_position') == [12, 34]
    assert service.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}


def test_new_desktop_import_copies_and_preserves_the_original(tmp_path):
    service = desktop(tmp_path)
    service.receive_text('客户档案系统')
    source = tmp_path / '客户档案系统.zip'
    with ZipFile(source, 'w') as stream:
        stream.writestr('isolated.txt', 'not executable')
    result = service.import_zip(source)
    assert source.exists()
    assert Path(result['zip']['path']).is_relative_to(Path(service.workspaces.settings()['archive_root']))
    assert service.get_project(1)['status'] == '待交付'


def test_project_name_can_be_corrected_without_changing_source_message(tmp_path):
    service = desktop(tmp_path)
    service.receive_text('客户档案系统')
    service.rename_project(1, '客户档案管理系统')
    assert service.get_project(1)['name'] == '客户档案管理系统'
    assert service.get_project(1)['raw_text'] == '客户档案系统'


def test_plain_title_list_followed_by_correct_count_is_not_lost(tmp_path):
    service = desktop(tmp_path)
    result = service.receive_text('客户档案系统\n合同归档工具\n2个项目')
    assert result['state'] == 'committed'
    assert service.db.counts()['projects'] == 2


def test_conflicting_batch_name_cannot_lose_pending_text(tmp_path):
    service = desktop(tmp_path)
    result = service.receive_text('需要核对的文字。')
    service.capture_message('旧项目', result['batch_name'])
    service.resolve_text(result['id'], [ParsedProject('新项目')])
    assert service.db.counts()['batches'] == 2
    assert service.db.counts()['projects'] == 2
