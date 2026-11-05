"""匿名真实归档链路：材料就绪不等于人工交接。"""
from pathlib import Path

from desktop_service import DesktopService


def task(service, task_id):
    return next(item for group in service.task_groups.snapshot(open_only=False)
                for item in group['tasks'] if item['id'] == task_id)


def test_real_material_delivery_classifies_and_readies_without_handoff(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    assert hasattr(service, 'archive_classification')
    types = {row['name']: row['id'] for row in service.list_project_types()}
    service.archive_classification.set_policy(True, 4, types['可运行'], types['生成材料'])
    received = service.task_groups.receive('1. 匿名交接集成系统')
    pid, tid = received['project_ids'][0], received['task_ids'][0]
    root = Path(service.workspaces.get_workspace(pid)['path'])
    for i in range(4):
        (root / f'资料{i}.txt').write_text(f'anonymous material {i}', encoding='utf-8')
    delivered = service.confirm_delivery(pid, confirmed=True)
    assert service.get_project(pid)['project_type_name'] == '可运行'
    assert task(service, tid)['material_ready']
    assert not task(service, tid)['handed_over']
    before = service.db.delivery_history()
    service.task_groups.handoff([tid], confirmed=True)
    assert task(service, tid)['handed_over']
    assert service.db.delivery_history() == before
    renewed = service.task_groups.receive('1. 匿名交接集成系统', new_round=True)
    second = renewed['task_ids'][0]
    assert not task(service, second)['material_ready']
    service.task_groups.bind_delivery([second], delivered['event_id'], confirmed=True)
    service.task_groups.handoff([second], confirmed=True)
    assert service.db.delivery_history() == before
    service.db.void_delivery_events([delivered['event_id']], confirmed=True)
    assert not task(service, tid)['material_ready']
    assert not task(service, tid)['handed_over']
    service.db.restore_delivery_events([delivered['event_id']], confirmed=True)
    assert not task(service, tid)['handed_over']
    assert service.get_project(pid)['status'] == '待交付'


def test_manual_same_type_intent_blocks_old_classification_undo(tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    assert hasattr(service, 'archive_classification')
    types = {row['name']: row['id'] for row in service.list_project_types()}
    policy = service.archive_classification
    policy.set_policy(False, 4, types['可运行'], types['生成材料'])
    pid = service.receive_text('1. 匿名分类集成系统')['project_ids'][0]
    root = Path(service.workspaces.get_workspace(pid)['path'])
    for i in range(4):
        (root / f'{i}.txt').write_text('anonymous', encoding='utf-8')
    service.confirm_delivery(pid, confirmed=True)
    operation = policy.apply(policy.preview()['token'], confirmed=True, enable_future=False)
    assert operation['changed_count'] == 1
    service.set_project_types([pid], types['可运行'])
    undone = policy.undo(operation['operation_id'], confirmed=True)
    assert undone['changed_count'] == 0 and undone['skipped']
    assert service.get_project(pid)['project_type_name'] == '可运行'
