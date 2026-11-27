"""Connect the signed-in original site to the desktop project's intake and delivery queue."""
from threading import Event
from time import monotonic
from sqlite3 import Error as DatabaseError

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QInputDialog, QMessageBox

from workbox.desktop.delivery_ui import beijing_timestamp
from workbox.desktop.dialog_lifecycle import transient_dialog
from workbox.projects.original_site import OriginalSiteProjects
from workbox.projects.project_names import original_site_name_match
from workbox.team_delivery.contracts import Outcome
from workbox.team_delivery.personal_matching import matching_personal_word_tasks
from workbox.team_delivery.site_attempt import site_account


class PersonalSiteWorkspace(QObject):
    _BINDING_REVIEW_NOTICE = 'personal-binding-review'

    def __init__(self, desktop, site):
        super().__init__(desktop)
        self.desktop = desktop
        self.site = site
        self.projects = OriginalSiteProjects(desktop.service.db, desktop.service.workspaces)
        self._snapshot = self._imported = self._in_flight = None
        self._superseded = Event()
        self._loading = False
        self._retry_after = {}
        self._unlinked = ()
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self.wake)
        site.personal_snapshot_changed.connect(self._observe)
        site.personal_file_requested.connect(self._receive)
        site.personal_delivery_finished.connect(self._submitted)
        desktop.panel.team_page.personal_binding_requested.connect(self._resolve_binding)
        site.enable_personal_workspace()

    def _observe(self, snapshot):
        previous = self._snapshot
        if snapshot != previous:
            self._superseded.set()
            self._superseded = Event()
            self._snapshot = snapshot
        if snapshot is None or (previous is not None and previous.account != snapshot.account):
            self._imported = None
            self._set_unlinked(())
        if snapshot is None:
            self._retry.stop()
        else:
            self.wake()

    def wake(self):
        """Import a fresh task list and choose at most one confirmed delivery for the browser."""
        snapshot = self.site.current_personal_snapshot()
        if (snapshot is None or snapshot != self._snapshot or self._loading or self._in_flight
                or self.desktop.closed or self.desktop.quitting):
            return
        stale = self._superseded
        needs_import = self._imported != snapshot
        now = monotonic()
        self._retry_after = {key: deadline for key, deadline in self._retry_after.items() if deadline > now}
        deferred = frozenset(self._retry_after)

        def prepare(*, cancel_event, **_):
            if stale.is_set() or cancel_event.is_set():
                return 0, None, None, False
            changed = self.desktop.service.sync_personal_site_snapshot(snapshot) if needs_import else 0
            unlinked = self.projects.unlinked_tasks(snapshot.account, snapshot.tasks) if needs_import else None
            for item in self.projects.pending(snapshot.account):
                if stale.is_set() or cancel_event.is_set():
                    break
                if item.event_id in deferred:
                    continue
                if item.status != 'pending':
                    return changed, unlinked, item, True
                current = next((task for task in snapshot.tasks if task.id == item.task_id), None)
                if current is None:
                    return changed, unlinked, item, True
                claim = self.projects.claim(item.event_id, snapshot.account, current)
                if claim is not None:
                    return changed, unlinked, claim, False
                # The title, owner or local delivery may have changed since enumeration.
            return changed, unlinked, None, False

        def complete(worker):
            self._loading = False
            if worker.error or worker.result is None:
                if worker.error and not stale.is_set() and self._available(snapshot):
                    self._message('待办同步失败', worker.error)
                if stale.is_set() and self._snapshot is not None:
                    self._retry.start(0)
                return
            changed, unlinked, item, verify_only = worker.result
            if changed:
                self.desktop.refresh()
            active = not stale.is_set() and self._available(snapshot)
            if active:
                self._imported = snapshot
                if unlinked is not None:
                    self._set_unlinked(unlinked)
            if item is not None:
                check_local = (lambda: self.projects.claim_is_current(item, snapshot.account)) if item.claim_token else None
                if active and self.site.submit_personal_delivery(
                        item.account_username, item.task_id, item.title, str(item.event_id),
                        verify_only=verify_only, resume_working=verify_only and bool(item.claim_token),
                        check_local=check_local,
                        round_id=item.round_id, source_revision=item.source_revision):
                    self._in_flight = item, verify_only
                else:
                    if not verify_only:
                        self.projects.mark_not_sent(item)
                    if active:
                        self._retry.start(1500)
            if stale.is_set() and self._snapshot is not None:
                self._retry.start(0)

        self._loading = True
        if not self.desktop.enqueue('同步这是家待办', prepare, silent=True,
                                    on_cancel=self._cancel_loading, on_complete=complete):
            self._loading = False

    def _available(self, snapshot):
        return (not self.desktop.quitting and not self.desktop.closed
                and self.site.current_personal_snapshot() == snapshot)

    def _cancel_loading(self):
        self._loading = False

    def _set_unlinked(self, tasks):
        previous_ids = tuple(task.id for task in self._unlinked[:6])
        ids = tuple(task.id for task in tasks[:6])
        self._unlinked = tasks
        page = self.desktop.panel.team_page
        page.set_unlinked_tasks(tasks)
        kind = self._BINDING_REVIEW_NOTICE
        if ids and ids != previous_ids:
            self._message('待办需要核对', '选择任务后点「关联本机项目…」。' +
                          '、'.join('#' + task_id for task_id in ids), kind=kind)
        elif not ids:
            page.clear_personal_result(kind)
            self.desktop.clear_notice(kind)

    def resolve_dropped_file(self, filename):
        """Ask for an upstream task number before ordinary intake could create an unbound namesake."""
        snapshot = self.site.current_personal_snapshot()
        if snapshot is None:
            return False
        matches = matching_personal_word_tasks(filename, snapshot)
        if not matches:
            return False
        if self._imported != snapshot:
            self._message('正在同步待办', '请稍后再拖入此文件。')
            return True
        if not any(task in self._unlinked for task in matches):
            return False
        self.desktop.panel.open_team_delivery()
        if self.desktop.panel.team_page.busy:
            self._message('正在核对待办', '请稍后再拖入此文件。')
        else:
            self.site.submit_file(filename, None)
        return True

    def intake_account(self) -> str | None:
        snapshot = self.site.current_personal_snapshot()
        return site_account(snapshot.account.username) if snapshot is not None else None

    def request_delivery_sync(self, project_id, task_id):
        snapshot = self.site.current_personal_snapshot()
        task = next((item for item in snapshot.tasks if item.id == task_id), None) if snapshot else None
        if task is None:
            self._message('暂时无法补同步', f'请用绑定的材料账号在「这是家」按编号 #{task_id} 核对当前任务。')
            return
        superseded = self._superseded
        changed_notice = '原站账号或任务已变化，请刷新后重试。'
        label = f'这是家 #{task.id} · {task.title}'

        def still_current():
            return not superseded.is_set() and self._available(snapshot)

        def preview_worker(**_):
            if superseded.is_set():
                raise ValueError(changed_notice)
            return self.projects.preview_delivery_sync(project_id, snapshot.account, task)

        def confirm(worker):
            if self.desktop.closed or self.desktop.quitting or worker.cancelled:
                return
            if not still_current():
                self._message('暂时无法补同步', changed_notice)
                return
            if worker.error:
                self._message('暂时无法补同步', worker.error)
                return
            preview = worker.result
            text = (f'{label}\n材料账号 {snapshot.account.username}\n'
                    f'本机交付时间（北京时间）\n{beijing_timestamp(preview.occurred_at)}\n\n'
                    '沿用这次交付的本机归档、记录和工作量统计。确认补同步这项任务？')
            with transient_dialog(QMessageBox(QMessageBox.Question, '补同步到这是家', text,
                                               QMessageBox.Yes | QMessageBox.No, self.desktop.panel)) as dialog:
                dialog.setTextFormat(Qt.PlainText)
                dialog.button(QMessageBox.Yes).setText('确认补同步')
                dialog.button(QMessageBox.No).setText('取消')
                dialog.setDefaultButton(QMessageBox.No)
                if dialog.exec() != QMessageBox.Yes:
                    return
            if not still_current():
                self._message('暂时无法补同步', changed_notice)
                return

            def queue_worker(**_):
                if superseded.is_set():
                    raise ValueError(changed_notice)
                return self.projects.queue_existing_delivery(project_id, snapshot.account, task,
                                                             expected_event_id=preview.event_id, confirmed=True)

            def queued(result):
                if self.desktop.closed or self.desktop.quitting or result.cancelled:
                    return
                if result.error:
                    self._message('暂时无法补同步', result.error if still_current() else changed_notice)
                    return
                if still_current():
                    self._message('已加入补同步队列', label)
                self.wake()

            self.desktop.enqueue('登记补同步任务', queue_worker, silent=True, on_complete=queued)

        self.desktop.enqueue('核对本机交付', preview_worker, silent=True, on_complete=confirm)

    def _resolve_binding(self, task, filename=None):
        snapshot = self.site.current_personal_snapshot()
        if snapshot is None or task not in snapshot.tasks or task not in self._unlinked:
            return
        candidates = [p for p in self.desktop.service.list_projects() if not p.get('original_task_id')]
        ranked = sorted(((original_site_name_match(p['name'], task.title), p) for p in candidates),
                        key=lambda item: item[0], reverse=True)
        choices = {f"#{p['id']} · {p['name']} · {p['batch_name']}": p['id']
                   for rank, p in ranked if rank}
        create = '新建独立本机项目'
        selected, accepted = QInputDialog.getItem(
            self.desktop.panel, '关联本机项目', f'这是家 #{task.id} · {task.title}\n请选择此任务对应的本机项目。',
            ['请选择', *choices, create], 0, False)
        if not accepted or selected == '请选择' or not self._available(snapshot):
            return
        generation = self._superseded

        def bind(**_):
            if generation.is_set():
                raise ValueError('原站待办已变化，请重新核对。')
            return self.desktop.service.bind_personal_site_task(
                snapshot.account, task, None if selected == create else choices[selected])

        def complete(worker):
            if not self._available(snapshot):
                return
            if worker.error or worker.cancelled:
                self._message('未关联本机项目', worker.error or '已取消。')
                return
            self._set_unlinked(tuple(item for item in self._unlinked if item != task))
            self.desktop.refresh()
            if filename:
                self._receive(filename, snapshot.account, task)
            else:
                self._message('已关联本机项目', f'{task.title} · #{task.id}')

        self.desktop.enqueue('关联原站任务', bind, on_complete=complete)

    def _receive(self, filename, account, task):
        snapshot = self.site.current_personal_snapshot()
        if snapshot is None or snapshot.account != account or task not in snapshot.tasks:
            self._message('待办已变化', '请刷新后重新拖入文件。')
            return
        if task in self._unlinked:
            self._resolve_binding(task, filename)
            return
        stale = self._superseded

        def receive(**options):
            if stale.is_set():
                raise ValueError('原站待办已变化，请刷新后重新拖入文件。')
            project_id = self.projects.binding_for_task(account, task)
            if project_id is None:
                raise ValueError('项目尚未与原站任务关联，请刷新待办后再试。')
            self.desktop.service.receive_file(filename, project_id, manual=True, **options)
            return self.desktop.service.get_project(project_id)['status']

        def complete(worker):
            self.desktop.refresh()
            if not self._available(snapshot):
                return
            if worker.error or worker.cancelled:
                self._message('文件未收好', worker.error or '已取消收件。')
            else:
                self._message(f'文件已收好 · {worker.result}', f'{task.title} · 原站 #{task.id}', intake_success=True)

        self.desktop.enqueue('收进原站任务文件', receive, on_complete=complete)

    def _submitted(self, result):
        if self._in_flight is None:
            return
        item, verify_only = self._in_flight
        if ((result.account_username, result.task_id, result.delivery_id)
                != (item.account_username, item.task_id, str(item.event_id))):
            return
        self._in_flight = None
        label = f'{item.title} · 原站 #{item.task_id}'
        current = self.site.current_personal_snapshot()
        visible = current is not None and site_account(current.account.username) == item.account_username
        try:
            if result.outcome == Outcome.SUBMITTED and result.receipt_id:
                saved = self.projects.record_receipt(item.event_id, account_username=item.account_username,
                                                     task_id=item.task_id, delivery_id=result.receipt_id)
                if saved and visible:
                    self._message('这是家已确认提交', label)
                if not saved:
                    self._retry_after[item.event_id] = monotonic() + 60
                    if visible:
                        self._message('原站结果待核对', '当前本地交付与回执不匹配。')
            else:
                if result.retryable and not verify_only:
                    self.projects.mark_not_sent(item)
                else:
                    self.projects.mark_needs_check(item, result.message)
                self._retry_after[item.event_id] = monotonic() + 60
                if visible:
                    self._message('本地已交付，原站待核对', label + '。' + result.message)
        except (OSError, ValueError, RuntimeError, DatabaseError) as error:
            self._retry_after[item.event_id] = monotonic() + 60
            self._message('保存原站结果失败', str(error))
        if not self.desktop.quitting:
            self._retry.start(0)

    def _message(self, title, detail, *, intake_success=False, kind='persistent'):
        self.desktop.panel.team_page.show_personal_result(title, detail, temporary=intake_success, kind=kind)
        self.desktop.notify(f'{title}。{detail}', kind='intake-success' if intake_success else kind)
