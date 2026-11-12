"""Re-dropping an old pending receipt rechecks current rules without duplicating it."""

import hashlib
import json

import pytest

from desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    return DesktopService(tmp_path / 'data' / 'workbox.db')


def old_receipt(service, text, state='needs-review'):
    with service.db.connect() as connection:
        return connection.execute(
            '''INSERT INTO intake_receipts
               (fingerprint,raw_text,candidates,state,reason,batch_name)
               VALUES (?,?,?,?,?,?)''',
            (hashlib.sha256(text.strip().encode()).hexdigest(), text,
             json.dumps([{'name': text.splitlines()[0], 'urgent': False}]),
             state, '旧规则需要核对', '匿名旧收件'),
        ).lastrowid


def test_explicit_retry_updates_old_candidates_and_commits_once(service):
    text = '匿名预约APP\n匿名查询APP'
    receipt_id = old_receipt(service, text)
    result = service.receive_text(text)
    assert result['receipt_id'] == receipt_id
    assert result['state'] == 'committed'
    assert not result['duplicate']
    assert [p['name'] for p in result['candidates']] == text.splitlines()
    assert len(result['created_project_ids']) == 2
    again = service.receive_text(text)
    assert again['duplicate']
    assert again['project_ids'] == result['project_ids']
    assert service.db.counts()['projects'] == 2
    with service.db.connect() as connection:
        assert connection.execute('SELECT count(*) FROM intake_receipts').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM batches').fetchone()[0] == 1


def test_retry_reuses_existing_app_identity(service):
    pid = service.capture_message('匿名预约APP', '匿名已有批次')['project_ids'][0]
    old_receipt(service, '匿名预约APP')
    result = service.receive_text('匿名预约APP')
    assert result['state'] == 'committed'
    assert result['reused_project_ids'] == [pid]
    assert result['created_project_ids'] == []
    assert service.get_project(pid)['batch_name'] == '匿名已有批次'


@pytest.mark.parametrize('boundary', ['archived', 'deleted', 'duplicate'])
def test_retry_preserves_identity_conflicts_without_partial_creation(service, boundary):
    pid = service.capture_message('匿名预约APP', '匿名已有批次')['project_ids'][0]
    if boundary == 'archived':
        service.bulk_update_projects([pid], archived=True)
    elif boundary == 'deleted':
        service.delete_project(pid, confirmed=True)
    else:
        service.capture_message('匿名预约APP', '匿名另一批次')
    before = service.db.counts()
    text = '匿名新建APP\n匿名预约APP'
    receipt_id = old_receipt(service, text)
    result = service.receive_text(text)
    assert result['receipt_id'] == receipt_id
    assert result['state'] == 'needs-review'
    assert not result['project_ids']
    assert result['reason'] != '旧规则需要核对'
    assert service.db.counts() == before


@pytest.mark.parametrize('state', ['committed', 'dismissed'])
def test_terminal_receipts_are_not_reprocessed(service, state):
    receipt_id = old_receipt(service, '匿名预约APP', state)
    result = service.receive_text('匿名预约APP')
    assert result['receipt_id'] == receipt_id and result['state'] == state
    assert result['duplicate']
    assert service.db.counts()['projects'] == 0


def test_retry_does_not_auto_accept_unclear_message(service):
    text = '请帮我确认一下匿名预约APP的需求'
    old_receipt(service, text)
    result = service.receive_text(text)
    assert result['state'] == 'needs-review'
    assert service.db.counts()['projects'] == 0


def test_retry_retains_legacy_task_message_links(service):
    text = '匿名预约APP'
    receipt_id = old_receipt(service, text)
    legacy = service.task_groups.receive(text)
    assert legacy['receipt_id'] == receipt_id and legacy['task_ids'] == []
    result = service.receive_text(text)
    assert result['state'] == 'committed'
    assert result['group_id'] == legacy['group_id']
    assert len(result['task_ids']) == 1
    assert service.task_groups.messages(legacy['group_id'])[0]['raw_text'] == text
    assert service.task_groups.snapshot()[0]['message_review_count'] == 0
