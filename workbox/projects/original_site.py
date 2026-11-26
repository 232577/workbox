"""Local project identity and durable submission state for original-site material tasks."""

from dataclasses import dataclass
from re import fullmatch
from uuid import uuid4

from workbox.projects.project_names import project_name_key
from workbox.projects.site_task_import import create_site_task_project, import_site_tasks, source_project_links
from workbox.team_delivery.contracts import Account, MATERIAL_PREPARATION_STAGES, Stage, Task
from workbox.team_delivery.site_attempt import site_account


_REVIEW = '任务已变化，请在原站按编号核对。'
_SUPERSEDED = '此交付已由更新的本地确认替代。'
_EDITABLE_PROJECT = '''
    d.archived_at IS NULL
    AND NOT EXISTS (SELECT 1 FROM project_deletions x WHERE x.project_id=p.id)
    AND NOT EXISTS (SELECT 1 FROM project_merges m WHERE m.source_id=p.id)
    AND NOT EXISTS (SELECT 1 FROM project_change_blocks x WHERE x.project_id=p.id)
'''
_AVAILABLE_BINDINGS = f'''
    SELECT b.* FROM original_site_active_projects b JOIN projects p ON p.id=b.active_project_id
    LEFT JOIN project_details d ON d.project_id=p.id
    WHERE b.account_username=? AND b.available=1 AND {_EDITABLE_PROJECT}
'''
_DELIVERY = '''
    SELECT s.*, e.project_id AS event_project_id,a.active_project_id,b.account_username,b.task_id,b.available,b.revision,
           b.title AS observed_title,b.stage AS observed_stage,b.round AS current_round
    FROM original_site_deliveries s JOIN original_site_bindings b ON b.project_id=s.project_id
    JOIN delivery_events e ON e.id=s.event_id
    LEFT JOIN original_site_active_projects a ON a.project_id=b.project_id
'''


@dataclass(frozen=True)
class SiteBinding:
    project_id: int
    account_username: str
    task_id: str
    title: str
    stage: Stage
    revision: str
    available: bool


@dataclass(frozen=True)
class SiteDelivery:
    event_id: int
    project_id: int
    account_username: str
    task_id: str
    title: str
    status: str
    receipt_id: str
    reason: str
    claim_token: str
    round_id: int
    source_revision: str


def _account(account):
    if (isinstance(account, Account) and account.role == 'material'
            and isinstance(account.id, str) and account.id and isinstance(account.username, str)):
        return site_account(account.username)
    return ''


def _task(account, task):
    if (not isinstance(task, Task) or task.owner_id != account.id
            or not isinstance(task.id, str) or not fullmatch(r'[1-9][0-9]{0,15}', task.id)
            or int(task.id) > 2**53 - 1 or not isinstance(task.stage, Stage)
            or not isinstance(task.title, str) or not 0 < len(task.title.strip()) <= 200
            or any(character in task.title.strip() for character in '\0\r\n')
            or not isinstance(task.revision, str) or not task.revision):
        raise ValueError('原站任务编号、标题、负责人或工序不完整。')
    return task


def _editable(connection, project_id):
    return connection.execute(f'''
        SELECT 1 FROM projects p LEFT JOIN project_details d ON d.project_id=p.id
        WHERE p.id=? AND {_EDITABLE_PROJECT}
    ''', (project_id,)).fetchone() is not None


def is_original_site_project(connection, project_id):
    """Keep personal-site confirmation rules after any explicit project merge."""
    return connection.execute('''
        SELECT 1 FROM original_site_bindings b JOIN project_canonical c ON c.project_id=b.project_id
        WHERE c.canonical_id=? LIMIT 1
    ''', (project_id,)).fetchone() is not None


def _latest_event(connection, project_id):
    return connection.execute('''
        SELECT e.id,e.kind FROM delivery_events e JOIN projects p ON p.id=e.project_id
        WHERE p.id=? AND p.status='已交付' AND p.delivered_at=e.occurred_at
          AND NOT COALESCE((SELECT a.voided FROM delivery_event_actions a
                            WHERE a.event_id=e.id ORDER BY a.id DESC LIMIT 1),0)
          AND NOT EXISTS (SELECT 1 FROM project_merges m
                          WHERE m.target_id=p.id AND m.created_at>=e.occurred_at)
        ORDER BY e.id DESC LIMIT 1
    ''', (project_id,)).fetchone()


def _remote_blocked(connection, row):
    return connection.execute('''
        SELECT 1 FROM original_site_deliveries s
        JOIN original_site_bindings b ON b.project_id=s.project_id
        WHERE b.task_id=? AND s.event_id!=? AND (
            s.status='needs_check' OR (s.status='completed' AND (b.account_username!=? OR s.round>=?))
        ) LIMIT 1
    ''', (row['task_id'], row['event_id'], row['account_username'], row['round'])).fetchone() is not None


def enqueue_delivery(connection, project_id, event_id):
    """Attach a real manual delivery inside the transaction that recorded it."""
    binding = connection.execute(
        'SELECT * FROM original_site_active_projects WHERE active_project_id=?', (project_id,)
    ).fetchone()
    if binding is None:
        return
    event = _latest_event(connection, project_id)
    if event is None or event['id'] != event_id or event['kind'] not in ('manual-files', 'manual-zip'):
        return
    round_id = binding['round']
    binding_project_id = binding['project_id']
    blocked = connection.execute('''
        SELECT 1 FROM original_site_deliveries
        WHERE project_id=? AND event_id!=? AND (status='needs_check' OR (round=? AND status='completed'))
    ''', (binding_project_id, event_id, round_id)).fetchone()
    if blocked:
        connection.execute('''
            INSERT OR IGNORE INTO original_site_deliveries(event_id,project_id,title,round,return_revision,status,reason)
            VALUES (?,?,?,?,?,'superseded','原站同轮已确认或上次请求仍待核对。')
        ''', (event_id, binding_project_id, binding['title'], round_id, binding['return_revision']))
        return
    connection.execute('''
        UPDATE original_site_deliveries SET status='superseded',reason=?
        WHERE project_id=? AND event_id!=? AND status='pending'
    ''', (_SUPERSEDED, binding_project_id, event_id))
    connection.execute('''
        INSERT INTO original_site_deliveries(event_id,project_id,title,round,return_revision,status)
        VALUES (?,?,?,?,?,'pending')
        ON CONFLICT(event_id) DO UPDATE SET status='pending',reason=''
        WHERE original_site_deliveries.status='superseded' AND original_site_deliveries.round=excluded.round
    ''', (event_id, binding_project_id, binding['title'], round_id, binding['return_revision']))


def _delivery(row, *, needs_review=False):
    return SiteDelivery(
        row['event_id'], row['event_project_id'], row['account_username'], row['task_id'], row['title'],
        'needs_check' if needs_review else row['status'], row['receipt_id'],
        row['reason'] or (_REVIEW if needs_review else ''), row['claim_token'],
        row['round'], row['claim_revision'],
    )


def _current_local_event(connection, row):
    project_id = row['event_project_id']
    if project_id != row['active_project_id'] or not _editable(connection, project_id):
        return False
    event = _latest_event(connection, project_id)
    return event is not None and event['id'] == row['event_id']


def _writable(connection, row):
    return (row['available'] and row['round'] == row['current_round']
            and row['title'] == row['observed_title'] and not _remote_blocked(connection, row))


class OriginalSiteProjects:
    def __init__(self, database, workspaces=None):
        self.db = database
        self.workspaces = workspaces

    @staticmethod
    def _observe(connection, username, task, project_id):
        previous = connection.execute('''
            SELECT round,return_revision FROM original_site_bindings WHERE task_id=? AND account_username=?
        ''', (task.id, username)).fetchone()
        available = task.stage in MATERIAL_PREPARATION_STAGES
        round_id = previous['round'] if previous else 1
        return_revision = previous['return_revision'] if previous else ''
        if task.stage is Stage.RETURNED and task.revision != return_revision:
            completed = connection.execute('''
                SELECT 1 FROM original_site_deliveries s
                JOIN original_site_bindings b ON b.project_id=s.project_id
                WHERE b.task_id=? AND b.account_username=? AND s.round=? AND s.status='completed'
                LIMIT 1
            ''', (task.id, username, round_id)).fetchone()
            known_return = connection.execute('''
                SELECT 1 FROM original_site_deliveries s
                JOIN original_site_bindings b ON b.project_id=s.project_id
                WHERE b.task_id=? AND b.account_username=? AND ? IN (s.claim_revision,s.return_revision) LIMIT 1
            ''', (task.id, username, task.revision)).fetchone()
            if completed and not known_return:
                round_id += 1
            if not known_return:
                return_revision = task.revision
        connection.execute('''
            UPDATE original_site_bindings SET available=0
            WHERE task_id=? AND account_username!=? AND available=1
        ''', (task.id, username))
        return connection.execute('''
            INSERT INTO original_site_bindings(account_username,task_id,project_id,title,revision,stage,round,
                                               return_revision,available)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(account_username,task_id) DO UPDATE SET title=excluded.title,revision=excluded.revision,
                stage=excluded.stage,round=excluded.round,return_revision=excluded.return_revision,
                available=excluded.available
            WHERE (title,revision,stage,round,return_revision,available) !=
                (excluded.title,excluded.revision,excluded.stage,excluded.round,
                 excluded.return_revision,excluded.available)
        ''', (username, task.id, project_id, task.title, task.revision, task.stage.value,
              round_id, return_revision, available)).rowcount

    def _ensure_workspaces(self, project_ids):
        if self.workspaces is not None:
            for start in range(0, len(project_ids), 500):
                self.workspaces.ensure_projects(project_ids[start:start + 500])

    def _bind_new_tasks(self, connection, existing, observed):
        waiting = {task.id: task for task in observed.values()
                   if task.id not in existing and task.stage in MATERIAL_PREPARATION_STAGES}
        if not waiting:
            return {}
        reassigned = {row['task_id'] for row in connection.execute(
            'SELECT DISTINCT task_id FROM original_site_bindings'
        ) if row['task_id'] in waiting}
        candidates = [(task_id, task.title) for task_id, task in waiting.items() if task_id not in reassigned]
        importing = {task_id for task_id, _ in candidates}
        result = import_site_tasks(connection, candidates, strict_identity=True, other_titles=(
            task.title for task in observed.values()
            if task.id not in importing and task.stage in MATERIAL_PREPARATION_STAGES
        ))
        conflicts = {row['task_id'] for row in result['conflicts']}
        projects = {task_id: project_id for task_id, project_id in source_project_links(connection).items()
                    if task_id in importing and task_id not in conflicts}
        if reassigned:
            created = {task_id: create_site_task_project(connection, task_id, waiting[task_id].title.strip())
                       for task_id in waiting if task_id in reassigned}
            self.db._assign_default_project_type(connection, list(created.values()))
            projects.update(created)
        return projects

    def sync(self, account, tasks):
        username = _account(account)
        if not username:
            return 0
        observed = {}
        for task in tasks:
            _task(account, task)
            if task.id in observed:
                raise ValueError('原站本人待办存在重复任务编号。')
            observed[task.id] = task
        changes, missing_workspaces = 0, []
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = {row['task_id']: row for row in connection.execute(
                '''SELECT b.*, a.active_project_id FROM original_site_bindings b
                   LEFT JOIN original_site_active_projects a ON a.project_id=b.project_id
                   WHERE b.account_username=?''', (username,)
            )}
            for task_id, row in existing.items():
                if task_id not in observed and row['available']:
                    changes += connection.execute(
                        'UPDATE original_site_bindings SET available=0 WHERE project_id=?', (row['project_id'],)
                    ).rowcount
            new_projects = self._bind_new_tasks(connection, existing, observed)
            for task_id, task in observed.items():
                row = existing.get(task_id)
                project_id = row['project_id'] if row else new_projects.get(task_id)
                if project_id is None:
                    continue
                connection.execute('INSERT OR IGNORE INTO site_task_project_links(task_id,project_id) VALUES (?,?)',
                                   (task_id, project_id))
                observed_change = self._observe(connection, username, task, project_id)
                changes += observed_change
                workspace_id = row['active_project_id'] if row else project_id
                if (self.workspaces is not None and task.stage in MATERIAL_PREPARATION_STAGES
                        and _editable(connection, workspace_id) and not connection.execute(
                            'SELECT 1 FROM project_workspaces WHERE project_id=?', (workspace_id,)
                        ).fetchone()):
                    missing_workspaces.append(workspace_id)
                    if not observed_change:
                        changes += 1
        self._ensure_workspaces(missing_workspaces)
        return changes

    def bind_existing(self, account, task, project_id, *, confirmed=False):
        username = _account(account)
        if confirmed is not True or not username:
            raise ValueError('请确认要关联的原站本人项目。')
        _task(account, task)
        if task.stage not in MATERIAL_PREPARATION_STAGES:
            raise ValueError('该原站项目当前不能关联。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.db._assert_projects_editable(connection, project_id)
            local = connection.execute('SELECT name FROM projects WHERE id=?', (project_id,)).fetchone()
            if project_name_key(local['name']) != project_name_key(task.title):
                raise ValueError('本地项目名称与原站项目全名不一致。')
            if connection.execute('''
                SELECT 1 FROM original_site_bindings b JOIN project_canonical c ON c.project_id=b.project_id
                WHERE c.canonical_id=? OR b.task_id=?
            ''', (project_id, task.id)).fetchone():
                raise ValueError('本地项目或原站编号已有绑定。')
            links = source_project_links(connection)
            if ((task.id in links and links[task.id] != project_id)
                    or any(identifier != task.id and pid == project_id for identifier, pid in links.items())):
                raise ValueError('本地项目或原站编号已有绑定。')
            connection.execute('INSERT OR IGNORE INTO site_task_project_links(task_id,project_id) VALUES (?,?)',
                               (task.id, project_id))
            self._observe(connection, username, task, project_id)
        self._ensure_workspaces([project_id])
        return project_id

    def create_for_selected(self, account, task, *, confirmed=False):
        """Create an independent project after the user confirms the exact upstream task."""
        username = _account(account)
        if confirmed is not True or not username:
            raise ValueError('请确认选中的原站本人任务。')
        _task(account, task)
        if task.stage not in MATERIAL_PREPARATION_STAGES:
            raise ValueError('该原站任务当前不能新建个人项目。')
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('''
                SELECT b.project_id,a.active_project_id FROM original_site_bindings b
                LEFT JOIN original_site_active_projects a ON a.project_id=b.project_id
                WHERE b.account_username=? AND b.task_id=?
            ''', (username, task.id)).fetchone()
            if existing:
                project_id = existing['active_project_id']
                if project_id is None:
                    raise ValueError('该编号的本机项目关联需要核对。')
                self.db._assert_projects_editable(connection, project_id)
            else:
                if connection.execute('''
                    SELECT 1 FROM site_task_project_links WHERE task_id=?
                    UNION ALL SELECT 1 FROM original_site_bindings WHERE task_id=? LIMIT 1
                ''', (task.id, task.id)).fetchone():
                    raise ValueError('该编号已有本机项目关联。请先核对并恢复原关联项目，再继续办理。')
                project_id = create_site_task_project(connection, task.id, task.title.strip())
                self.db._assign_default_project_type(connection, [project_id])
                connection.execute('INSERT INTO site_task_project_links(task_id,project_id) VALUES (?,?)',
                                   (task.id, project_id))
            self._observe(connection, username, task, existing['project_id'] if existing else project_id)
        self._ensure_workspaces([project_id])
        return project_id

    def unlinked_tasks(self, account, tasks):
        username = _account(account)
        if not username:
            return ()
        eligible = tuple(task for task in (_task(account, item) for item in tasks)
                         if task.stage in MATERIAL_PREPARATION_STAGES)
        if not eligible:
            return ()
        with self.db.connect() as connection:
            current = {(row['task_id'], row['title'], row['stage'], row['revision'])
                       for row in connection.execute(_AVAILABLE_BINDINGS, (username,))}
        return tuple(task for task in eligible if (task.id, task.title, task.stage.value, task.revision) not in current)

    def binding_for(self, project_id, account=None):
        with self.db.connect() as connection:
            row = connection.execute(
                'SELECT * FROM original_site_active_projects WHERE active_project_id=?', (project_id,)
            ).fetchone()
            if row is None or (account is not None and _account(account) != row['account_username']):
                return None
            return SiteBinding(
                project_id, row['account_username'], row['task_id'], row['title'], Stage(row['stage']),
                row['revision'], bool(row['available'] and _editable(connection, project_id)),
            )

    def binding_for_task(self, account, task):
        username = _account(account)
        if not username or task is None:
            return None
        _task(account, task)
        if task.stage not in MATERIAL_PREPARATION_STAGES:
            return None
        with self.db.connect() as connection:
            row = connection.execute(
                _AVAILABLE_BINDINGS + ' AND b.task_id=? AND b.title=? AND b.stage=? AND b.revision=?',
                (username, task.id, task.title, task.stage.value, task.revision),
            ).fetchone()
            return row['active_project_id'] if row else None

    def pending(self, account):
        username = _account(account)
        if not username:
            return ()
        with self.db.connect() as connection:
            rows = connection.execute(
                _DELIVERY + " WHERE b.account_username=? AND s.status IN ('pending','needs_check') ORDER BY s.event_id",
                (username,),
            ).fetchall()
            return tuple(
                _delivery(row, needs_review=row['status'] == 'pending' and not _writable(connection, row))
                for row in rows if row['status'] == 'needs_check' or _current_local_event(connection, row)
            )

    def delivery_for_event(self, event_id):
        with self.db.connect() as connection:
            row = connection.execute(_DELIVERY + ' WHERE s.event_id=?', (event_id,)).fetchone()
            return _delivery(row) if row else None

    def claim(self, event_id, account, current_task):
        username = _account(account)
        if not username or current_task is None:
            return None
        _task(account, current_task)
        if current_task.stage not in MATERIAL_PREPARATION_STAGES:
            return None
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(_DELIVERY + ' WHERE s.event_id=?', (event_id,)).fetchone()
            if (row is None or row['account_username'] != username or row['task_id'] != current_task.id
                    or row['title'] != current_task.title or row['revision'] != current_task.revision
                    or row['observed_stage'] != current_task.stage.value
                    or row['status'] != 'pending' or not _current_local_event(connection, row)
                    or not _writable(connection, row)):
                return None
            connection.execute('''
                UPDATE original_site_deliveries SET status='needs_check',claim_token=?,claim_revision=?,reason=?
                WHERE event_id=?
            ''', (uuid4().hex, current_task.revision, '原站请求待回执。', event_id))
            return _delivery(connection.execute(_DELIVERY + ' WHERE s.event_id=?', (event_id,)).fetchone())

    def claim_is_current(self, claim, account):
        """Recheck local eligibility before the caller sends a network phase."""
        if not claim.claim_token or claim.account_username != _account(account):
            return False
        with self.db.connect() as connection:
            connection.execute('BEGIN')
            row = connection.execute(_DELIVERY + ' WHERE s.event_id=?', (claim.event_id,)).fetchone()
            return bool(row and row['status'] == 'needs_check' and row['claim_token'] == claim.claim_token
                        and _current_local_event(connection, row) and _writable(connection, row))

    def mark_not_sent(self, claim):
        if not claim.claim_token:
            return False
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(_DELIVERY + ' WHERE s.event_id=?', (claim.event_id,)).fetchone()
            if row is None or row['status'] != 'needs_check' or row['claim_token'] != claim.claim_token:
                return False
            if _current_local_event(connection, row):
                status, reason = 'pending', ''
            else:
                status, reason = 'superseded', _SUPERSEDED
            changed = connection.execute('''
                UPDATE original_site_deliveries SET status=?, claim_token='',claim_revision='',reason=?
                WHERE event_id=? AND status='needs_check' AND claim_token=?
            ''', (status, reason, claim.event_id, claim.claim_token)).rowcount
            if changed:
                project_id = row['active_project_id']
                latest = _latest_event(connection, project_id)
                if latest and latest['id'] != claim.event_id:
                    enqueue_delivery(connection, project_id, latest['id'])
            return bool(changed)

    def mark_needs_check(self, claim, reason):
        if not claim.claim_token:
            return False
        with self.db.connect() as connection:
            return bool(connection.execute('''
                UPDATE original_site_deliveries SET reason=?
                WHERE event_id=? AND status='needs_check' AND claim_token=?
            ''', (str(reason)[:500] or _REVIEW, claim.event_id, claim.claim_token)).rowcount)

    def record_receipt(self, event_id, *, account_username, task_id, delivery_id):
        if (not isinstance(account_username, str) or not isinstance(delivery_id, str)
                or not delivery_id.strip() or len(delivery_id) > 500):
            return False
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(_DELIVERY + ' WHERE s.event_id=?', (event_id,)).fetchone()
            if (row is None or row['account_username'] != site_account(account_username)
                    or row['task_id'] != task_id):
                return False
            if row['status'] == 'completed':
                return row['receipt_id'] == delivery_id
            if row['status'] != 'needs_check' and (
                    row['status'] != 'pending' or row['round'] != row['current_round']
                    or not _current_local_event(connection, row) or _remote_blocked(connection, row)):
                return False
            connection.execute('''
                UPDATE original_site_deliveries SET status='completed',claim_token='',receipt_id=?,reason=''
                WHERE event_id=?
            ''', (delivery_id, event_id))
            return True
