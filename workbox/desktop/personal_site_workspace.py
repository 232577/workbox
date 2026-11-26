"""Connect the signed-in original site to the desktop project's intake and delivery queue."""
from threading import Event
from time import monotonic
from sqlite3 import Error as DatabaseError

from PySide6.QtCore import QObject, QTimer

from workbox.projects.original_site import OriginalSiteProjects
from workbox.team_delivery.contracts import Outcome
from workbox.team_delivery.site_attempt import site_account


class PersonalSiteWorkspace(QObject):
    def __init__(self, desktop, site):
        super().__init__(desktop)
        self.desktop = desktop
        self.site = site
        self.projects = OriginalSiteProjects(desktop.service.db, desktop.service.workspaces)
        self._snapshot = self._imported = self._in_flight = None
        self._superseded = Event()
        self._loading = False
        self._retry_after = {}
        self._retry = QTimer(self, singleShot=True)
        self._retry.timeout.connect(self.wake)
        site.personal_snapshot_changed.connect(self._observe)
        site.personal_file_requested.connect(self._receive)
        site.personal_delivery_finished.connect(self._submitted)
        site.enable_personal_workspace()

    def _observe(self, snapshot):
        if snapshot != self._snapshot:
            self._superseded.set()
            self._superseded = Event()
            self._snapshot = snapshot
        if snapshot is None:
            self._imported = None
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
                return 0, None, False
            changed = self.projects.sync(snapshot.account, snapshot.tasks) if needs_import else 0
            for item in self.projects.pending(snapshot.account):
                if stale.is_set() or cancel_event.is_set():
                    break
                if item.event_id in deferred:
                    continue
                if item.status != 'pending':
                    return changed, item, True
                current = next((task for task in snapshot.tasks if task.id == item.task_id), None)
                if current is None:
                    return changed, item, True
                claim = self.projects.claim(item.event_id, snapshot.account, current)
                if claim is not None:
                    return changed, claim, False
                # The title, owner or local delivery may have changed since enumeration.
            return changed, None, False

        def complete(worker):
            self._loading = False
            if worker.error:
                self._message('待办同步失败', worker.error)
                return
            if worker.result is None:
                return
            changed, item, verify_only = worker.result
            if changed:
                self.desktop.refresh()
            active = not stale.is_set() and self._available(snapshot)
            if active:
                self._imported = snapshot
            if item is not None:
                check_local = None if verify_only else lambda: self.projects.claim_is_current(item, snapshot.account)
                if active and self.site.submit_personal_delivery(
                        item.account_username, item.task_id, item.title, str(item.event_id),
                        verify_only=verify_only, check_local=check_local,
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

    def _receive(self, filename, account, task):
        snapshot = self.site.current_personal_snapshot()
        if snapshot is None or snapshot.account != account or task not in snapshot.tasks:
            self._message('待办已变化', '请刷新后重新拖入文件。')
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
                self._message(f'文件已收好 · {worker.result}', f'{task.title} · 原站 #{task.id}')

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

    def _message(self, title, detail):
        self.desktop.panel.team_page.show_personal_result(title, detail)
        self.desktop.notify(f'{title}。{detail}')
