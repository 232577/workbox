"""用原站工序处理一件普通材料，保存本机原件及操作回执。"""

from pathlib import Path
import sqlite3
from threading import Lock
from collections.abc import Callable

from workbox.team_delivery.contracts import (
    MATERIAL_PREPARATION_STAGES, Account, DropResult, Gateway, GatewayError, Material, Outcome, Stage, Task,
)
from workbox.team_delivery.storage import Journal, MaterialStore, Operation, StorageError


_WRITABLE = (Stage.READY, Stage.WORKING)


class _Stop(Exception):
    def __init__(self, message, outcome=None, *, account_changed=False):
        super().__init__(message)
        self.outcome = outcome
        self.account_changed = account_changed


def _account_key(account):
    return (account.id, account.role, account.session) if account is not None else None


class WorkflowService:
    """网关按账号、任务版本和请求号原子授权、校验并去重。"""

    def __init__(self, gateway: Gateway, material_root: Path, journal_path: Path, *, prepare_only: bool = False,
                 check_preparation_account: Callable[[], None] | None = None):
        self._gateway = gateway
        self._store = MaterialStore(material_root)
        self._journal = Journal(journal_path)
        self._prepare_only = prepare_only
        self._accepted_stages = MATERIAL_PREPARATION_STAGES if prepare_only else _WRITABLE
        self._check_preparation_account = check_preparation_account
        self._lock = Lock()

    def submit_file(self, path: Path, task_id: str | None, selected_account: Account | None) -> DropResult:
        with self._lock:
            return self._submit_file(path, task_id, selected_account)

    def _submit_file(self, path, task_id, selected_account):
        material = None
        operation = None
        try:
            snapshot = self._gateway.snapshot()
            account = snapshot.account
            if account is None or not account.id or not account.session or account.role != 'material':
                raise _Stop('请使用当前有材料职责的账号登录。')
            if selected_account is not None and _account_key(account) != _account_key(selected_account):
                raise _Stop('当前账号已变化，请重新确认任务。', account_changed=True)
            automatic_selection = task_id is None
            selection, reconciliation_only = self._select(path, task_id, snapshot)
            task = self._current(account, selection)
            task_id = task.id
            if task.title != path.stem and not reconciliation_only:
                raise _Stop('文件名须与当前项目全名一致，请重新核对。', Outcome.NEEDS_SELECTION)
            if self._prepare_only:
                journal_path = self._journal.path.resolve()
                if journal_path.drive.startswith('\\\\') or journal_path.is_relative_to(self._store.root):
                    raise _Stop('请选择本机交件记录目录，共享目录只保存原件。')
            operation = self._journal.get(account.id, task.id)
            if task.stage is not Stage.COMPLETED:
                self._require_owner(account, task)
                if reconciliation_only:
                    raise _Stop('当前文件无法唯一确认任务，请在面板中核对。', Outcome.NEEDS_SELECTION)
            elif self._prepare_only or operation is None:
                raise _Stop('这项材料已有完成状态，请核对原站交接记录。')
            if self._prepare_only and operation is not None and (operation.phase != 'saved' or operation.receipt_id is not None):
                raise _Stop('本机已有网页动作记录，请先核对原站状态。')

            with self._store.stage_word(path) as staged:
                if self._prepare_only:
                    if not self._journal.reserve_preparation(account, task, staged, automatic_selection):
                        raise _Stop('同一原件已有其他任务的记录，请明确选择本次任务。', Outcome.NEEDS_SELECTION)
                    self._recheck_preparation(account, task, staged.name)
                    material = self._store.archive_word(task.id, staged)
                else:
                    if operation is None:
                        operation = self._journal.reserve(account, task, staged, automatic_selection)
                        if operation is None:
                            raise _Stop('同一原件已有其他任务的记录，请明确选择本次任务。', Outcome.NEEDS_SELECTION)
                    elif automatic_selection and self._journal.has_other_task(account.id, task.id, staged):
                        raise _Stop('同一原件已有其他任务的记录，请明确选择本次任务。', Outcome.NEEDS_SELECTION)
                    if operation.sha256 != staged.sha256 or operation.name != staged.name:
                        raise _Stop('此任务已有另一份交件记录，请核对已保存的原件。', Outcome.NEEDS_SELECTION)
                    material = self._store.material(operation, staged if operation.phase == 'saved' else None)
            if self._prepare_only:
                return self._prepared_result(account, task, material)
            return self._advance(account, operation, material)
        except _Stop as error:
            outcome = error.outcome or (Outcome.PENDING if material is not None else Outcome.BLOCKED)
            if error.account_changed:
                return DropResult(outcome, str(error))
            return DropResult(outcome, str(error), task_id, material)
        except StorageError as error:
            return DropResult(Outcome.PENDING if operation is not None or material is not None else Outcome.BLOCKED,
                              str(error), task_id, material)
        except (GatewayError, OSError, sqlite3.Error):
            return DropResult(
                Outcome.PENDING if operation is not None or material is not None else Outcome.BLOCKED,
                '暂无法确认授权、原件保存或原站结果，请核对后再试。', task_id, material)

    def _select(self, path, selected_id, snapshot):
        matches = {task.id for task in snapshot.tasks
                   if task.title == path.stem and task.owner_id == snapshot.account.id and task.stage in self._accepted_stages}
        if selected_id is not None:
            if matches and selected_id not in matches:
                raise _Stop('文件名与所选任务冲突，请核对。', Outcome.NEEDS_SELECTION)
            return selected_id, False
        if len(matches) > 1:
            raise _Stop('有多个同名项目，请选择本次任务。', Outcome.NEEDS_SELECTION)
        if len(matches) == 1:
            return next(iter(matches)), False
        # 已完成项目可能离开待办。旧记录只能用于回读已发生的提交。
        previous = self._journal.find_saved(snapshot.account.id, path.name)
        if len(previous) == 1:
            return previous[0].task_id, True
        raise _Stop('没有唯一匹配的当前项目，请核对项目全名。', Outcome.NEEDS_SELECTION)

    def _require_account(self, account):
        try:
            read_account = getattr(self._gateway, 'current_account', None)
            observed = read_account() if callable(read_account) else self._gateway.snapshot().account
        except (GatewayError, OSError):
            raise _Stop('暂无法核对当前原站账号，请重新确认。', account_changed=True) from None
        if _account_key(observed) != _account_key(account):
            raise _Stop('当前账号已变化，请使用原账号重新核对。', account_changed=True)

    def _current(self, account, task_id):
        self._require_account(account)
        try:
            task = self._gateway.task(task_id)
        except (GatewayError, OSError):
            if self._prepare_only:
                self._require_account(account)
            raise
        if self._prepare_only:
            self._require_account(account)
        if task is None or task.id != task_id or not task.revision:
            raise _Stop('原站任务或版本暂不可核对。')
        return task

    def _require_owner(self, account, task, name=None):
        if task.owner_id != account.id or task.stage not in self._accepted_stages:
            raise _Stop('责任人或材料工序已变化，请在原站核对。')
        if name is not None and task.title != Path(name).stem:
            raise _Stop('项目全名已变化，请核对本次材料。')

    def _require_saved_version(self, operation, task):
        if (task.stage.value, task.revision) != (operation.initial_stage, operation.revision):
            raise _Stop('保存期间任务版本已变化，请核对原件和当前工序。')

    def _recheck_preparation(self, account, observed: Task, name: str) -> Task:
        if self._check_preparation_account is not None:
            try:
                self._check_preparation_account()
            except GatewayError as error:
                raise _Stop(str(error), Outcome.BLOCKED, account_changed=True) from error
        task = self._current(account, observed.id)
        self._require_owner(account, task, name)
        if (task.stage, task.revision) != (observed.stage, observed.revision):
            raise _Stop('保存期间任务版本已变化，请核对原件和当前工序。')
        self._require_account(account)
        return task

    def _prepared_result(self, account, observed: Task, material: Material) -> DropResult:
        task = self._recheck_preparation(account, observed, material.name)
        relative_path = material.path.relative_to(self._store.root)
        self._journal.record_preparation(account, task, material, relative_path)
        self._require_account(account)
        return DropResult(Outcome.PENDING, '原件已保存，待网页提交', task.id, material,
                          prepared=True, prepared_task=task)

    def _advance(self, account, operation: Operation, material: Material):
        task = self._current(account, operation.task_id)
        if task.stage is Stage.COMPLETED:
            return self._finish(account, operation, material, task)
        self._require_owner(account, task, operation.name)
        if operation.receipt_id is not None:
            raise _Stop('当前工序与已确认的交接记录不一致，请核对。')
        if operation.phase == 'saved':
            self._require_saved_version(operation, task)
            phase = 'begin' if task.stage is Stage.READY else 'submit'
            operation = self._journal.advance(operation, phase, task.revision)
        if operation.phase == 'begin':
            operation = self._begin(account, operation, task)
        return self._submit_material(account, operation)

    def _begin(self, account, operation, task):
        if task.stage is Stage.READY and task.revision != operation.revision:
            raise _Stop('原站开始制作前的版本已变化，请核对。')
        # 重启后若已进入进行中，用原版本和同一请求核实此前的开始动作。
        expected = Task(operation.task_id, Path(operation.name).stem, account.id, Stage.READY, operation.revision)
        self._require_account(account)
        try:
            begun_revision = self._gateway.begin(account, task if task.stage is Stage.READY else expected,
                                                 operation.request_id + '-begin')
        except (GatewayError, OSError):
            refreshed = self._current(account, operation.task_id)
            self._require_owner(account, refreshed, operation.name)
            raise _Stop('原件已保存，开始制作的结果待核对。')
        working = self._current(account, operation.task_id)
        self._require_owner(account, working, operation.name)
        if (not isinstance(begun_revision, str) or not begun_revision.strip()
                or working.stage is not Stage.WORKING or working.revision == operation.revision
                or begun_revision != working.revision):
            raise _Stop('原件已保存，尚不能确认原站已进入进行中。')
        advanced = self._journal.advance(operation, 'submit', working.revision)
        if advanced.phase != 'submit' or advanced.revision != working.revision:
            raise _Stop('交件记录的提交依据已变化，请核对。')
        return advanced

    def _submit_material(self, account, operation):
        # 重发时文件、提交版本和请求号都不更新。
        material = self._store.material(operation)
        task = self._current(account, operation.task_id)
        if task.stage is Stage.COMPLETED:
            return self._finish(account, operation, material, task)
        self._require_owner(account, task, operation.name)
        if (operation.phase != 'submit' or operation.receipt_id is not None
                or task.stage is not Stage.WORKING or task.revision != operation.revision):
            raise _Stop('原件已保存，原站提交依据已变化，请核对。')
        try:
            self._gateway.submit(account, task, operation.request_id + '-submit', material)
        except (GatewayError, OSError):
            # 连接中断也可能在服务器留下成功交接，统一用原站读回确认。
            pass
        return self._finish(account, operation, material, self._current(account, operation.task_id))

    def _finish(self, account, operation, material, task):
        # 另一个本机窗口可能已将同一请求推进到提交阶段。
        operation = self._journal.get(account.id, task.id)
        handoff = task.handoff
        if (operation is None or operation.phase != 'submit' or task.stage is not Stage.COMPLETED or handoff is None
                or not handoff.id.strip() or handoff.actor_id != account.id
                or handoff.request_id != operation.request_id + '-submit'):
            raise _Stop('原件已保存，未核实本次材料交接记录。')
        self._require_account(account)
        if not self._journal.confirm(operation, handoff.id):
            raise _Stop('原站交接回执与本机记录不一致，请核对。')
        return DropResult(Outcome.SUBMITTED, '原站已确认材料提交。', operation.task_id, material, handoff.id)
