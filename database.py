"""Small, local SQLite store. Every connection is short-lived and thread-local."""

from __future__ import annotations

import sqlite3
import re
import json
import hashlib
import os
from datetime import date, datetime, time, timedelta, timezone
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path, PurePosixPath
from typing import Iterator

STATUSES = ("待开发", "开发中", "待交付", "已交付")
DELIVERY_TIMEZONE = timezone(timedelta(hours=8))

SCHEMA = """
BEGIN;
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK(length(trim(name)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    raw_text TEXT NOT NULL CHECK(length(trim(raw_text)) > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    status TEXT NOT NULL DEFAULT '待开发'
        CHECK(status IN ('待开发','开发中','待交付','已交付')),
    urgent INTEGER NOT NULL DEFAULT 0 CHECK(urgent IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    delivered_at TEXT,
    delivered_zip_id INTEGER REFERENCES zips(id)
);
CREATE TABLE IF NOT EXISTS zips (
    id INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size INTEGER NOT NULL CHECK(size > 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    mtime_ns INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(path, sha256)
);
CREATE INDEX IF NOT EXISTS idx_projects_batch ON projects(batch_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_message ON projects(message_id);
CREATE INDEX IF NOT EXISTS idx_zips_project ON zips(project_id);
CREATE TABLE IF NOT EXISTS project_details (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id),
    notes TEXT NOT NULL DEFAULT '',
    due_date TEXT,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS zip_bundles (
    zip_id INTEGER PRIMARY KEY REFERENCES zips(id),
    declared_count INTEGER,
    member_names TEXT NOT NULL,
    match_state TEXT NOT NULL CHECK(match_state IN ('matched','needs-review')),
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS zip_bundle_members (
    zip_id INTEGER NOT NULL REFERENCES zips(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    PRIMARY KEY(zip_id,project_id)
);
CREATE INDEX IF NOT EXISTS idx_zip_bundle_project ON zip_bundle_members(project_id);
CREATE VIEW IF NOT EXISTS zip_project_links AS
    SELECT id AS zip_id,project_id FROM zips WHERE project_id IS NOT NULL
    UNION SELECT zip_id,project_id FROM zip_bundle_members;
CREATE TABLE IF NOT EXISTS zip_project_approvals (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id),
    zip_id INTEGER NOT NULL REFERENCES zips(id)
);
CREATE TABLE IF NOT EXISTS zip_approval_migrations (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS zip_project_file_counts (
    zip_id INTEGER NOT NULL REFERENCES zips(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    file_count INTEGER NOT NULL CHECK(typeof(file_count)='integer' AND file_count >= 0),
    decided_status TEXT NOT NULL CHECK(decided_status IN ('待交付','已交付')),
    rule TEXT NOT NULL CHECK(rule='zip-file-count-v1'),
    checked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(zip_id,project_id)
);
CREATE TABLE IF NOT EXISTS delivery_events (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    project_name TEXT NOT NULL,
    batch_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('auto-zip','manual-zip','manual-files','legacy')),
    zip_id INTEGER REFERENCES zips(id),
    archive_path TEXT NOT NULL,
    archive_signature TEXT,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    legacy_source_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_delivery_events_time ON delivery_events(occurred_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_events_project ON delivery_events(project_id,occurred_at DESC,id DESC);
CREATE TRIGGER IF NOT EXISTS delivery_events_no_update BEFORE UPDATE ON delivery_events
BEGIN SELECT RAISE(ABORT,'交付事实仅允许追加，不能修改。'); END;
CREATE TRIGGER IF NOT EXISTS delivery_events_no_delete BEFORE DELETE ON delivery_events
BEGIN SELECT RAISE(ABORT,'交付事实仅允许追加，不能删除。'); END;
CREATE TABLE IF NOT EXISTS delivery_event_actions (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES delivery_events(id),
    voided INTEGER NOT NULL CHECK(voided IN (0,1)),
    reason TEXT NOT NULL DEFAULT '' CHECK(length(reason)<=1000),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_delivery_event_actions_event ON delivery_event_actions(event_id,id DESC);
CREATE TRIGGER IF NOT EXISTS delivery_event_actions_no_update BEFORE UPDATE ON delivery_event_actions
BEGIN SELECT RAISE(ABORT,'交付管理记录仅允许追加，不能修改。'); END;
CREATE TRIGGER IF NOT EXISTS delivery_event_actions_no_delete BEFORE DELETE ON delivery_event_actions
BEGIN SELECT RAISE(ABORT,'交付管理记录仅允许追加，不能删除。'); END;
CREATE TABLE IF NOT EXISTS delivery_event_migrations (
    name TEXT PRIMARY KEY
);
PRAGMA user_version = 1;
COMMIT;
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"不支持的数据库版本 {version}，未修改已有数据。")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.execute('BEGIN IMMEDIATE')
            self._migrate_delivery_events(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("projects", "batches", "zips")
            }

    @staticmethod
    def _migrate_delivery_events(connection: sqlite3.Connection) -> None:
        """只迁移升级前仍保存着具体交付时间的当前事实，不推测已清除历史。"""
        marker = 'saved-current-deliveries-v1'
        if connection.execute('SELECT 1 FROM delivery_event_migrations WHERE name=?', (marker,)).fetchone():
            return
        rows = connection.execute('''SELECT p.id,p.name,b.name AS batch_name,p.delivered_at,
            p.delivered_zip_id,COALESCE(z.path,'') AS archive_path
            FROM projects p JOIN batches b ON b.id=p.batch_id
            LEFT JOIN zips z ON z.id=p.delivered_zip_id
            WHERE p.status='已交付' AND p.delivered_at IS NOT NULL''').fetchall()
        for row in rows:
            try:
                saved = datetime.fromisoformat(row['delivered_at'].replace('Z', '+00:00'))
            except (ValueError, TypeError):
                continue
            if saved.tzinfo is None or saved.utcoffset() != timedelta(0):
                continue
            occurred_at = saved.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            if connection.execute('SELECT 1 FROM delivery_events WHERE project_id=? AND occurred_at=?',
                                  (row['id'], occurred_at)).fetchone():
                continue
            connection.execute('''INSERT OR IGNORE INTO delivery_events(
                project_id,project_name,batch_name,kind,zip_id,archive_path,occurred_at,legacy_source_key)
                VALUES (?,?,?,'legacy',?,?,?,?)''',
                (row['id'], row['name'], row['batch_name'], row['delivered_zip_id'], row['archive_path'],
                 occurred_at, 'saved-current-project:' + str(row['id'])))
        connection.execute('INSERT INTO delivery_event_migrations(name) VALUES (?)', (marker,))

    @staticmethod
    def _delivery_day_bounds(day: date | None):
        current = datetime.now(DELIVERY_TIMEZONE).date() if day is None else day
        if type(current) is not date:
            raise ValueError('交付统计日期必须是日期。')
        start = datetime.combine(current, time.min, tzinfo=DELIVERY_TIMEZONE).astimezone(timezone.utc)
        finish = start + timedelta(days=1)
        return (current.isoformat(), start.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                finish.isoformat(timespec='milliseconds').replace('+00:00', 'Z'))

    def today_delivery_summary(self, day: date | None = None) -> dict:
        """项目只在全历史首次有效交付的北京日期计数；补交和撤销仍保留。"""
        current, _, _ = self._delivery_day_bounds(day)
        result = self.delivery_history(date.fromisoformat(current))
        projects = {}
        for event in result['events']:
            pid = event['project_id']
            if pid not in projects:
                projects[pid] = dict(project_id=pid, project_name=event['project_name'],
                    batch_name=event['batch_name'], delivery_count=0, latest_at=event['occurred_at'],
                    archive_path=event['archive_path'], kind=event['kind'], counted=False,
                    first_at=event['first_at'], repeat_count=0, voided_count=0,
                    last_action_id=event['last_action_id'], last_action=event['last_action'],
                    management_reason=event['management_reason'], managed_at=event['managed_at'])
            projects[pid]['delivery_count'] += 1
            projects[pid]['counted'] |= event['counted']
            projects[pid]['repeat_count'] += event['counting_role'] == 'repeat'
            projects[pid]['voided_count'] += event['voided']
        return {key: value for key, value in result.items() if key != 'events'} | dict(projects=list(projects.values()))

    def delivery_events_for_project(self, project_id: int, day: date | None = None) -> list[dict]:
        if type(project_id) is not int or project_id <= 0:
            raise ValueError('项目编号无效。')
        current, _, _ = self._delivery_day_bounds(day)
        return self.delivery_history(date.fromisoformat(current), project_id=project_id)['events']

    def delivery_history(self, day: date | None = None, keyword: str = '', project_id: int | None = None) -> dict:
        """day=None 查询全部历史。排序和首次认定始终在筛选之前按真实时间及 ID 计算。"""
        if not isinstance(keyword, str) or len(keyword) > 200:
            raise ValueError('交付历史关键词不能超过 200 个字符。')
        if project_id is not None and (type(project_id) is not int or project_id <= 0):
            raise ValueError('项目编号无效。')
        current, conditions, parameters = None, [], []
        if day is not None:
            current, start, finish = self._delivery_day_bounds(day)
            conditions.append('julianday(e.occurred_at)>=julianday(?) AND julianday(e.occurred_at)<julianday(?)')
            parameters.extend((start, finish))
        if project_id is not None:
            conditions.append('e.project_id=?')
            parameters.append(project_id)
        keyword = keyword.strip()
        if keyword:
            conditions.append('(instr(lower(e.project_name),lower(?))>0 OR instr(lower(e.batch_name),lower(?))>0)')
            parameters.extend((keyword, keyword))
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.connect() as connection:
            # 撤销状态来自独立追加日志，不改写真实交付事件或文件关联。
            rows = connection.execute('''WITH effective AS (
                SELECT d.*,COALESCE(a.voided,0) AS voided,a.id AS last_action_id,
                    CASE WHEN a.id IS NULL THEN NULL WHEN a.voided=1 THEN 'void' ELSE 'restore' END AS last_action,
                    COALESCE(a.reason,'') AS management_reason,a.created_at AS managed_at
                FROM delivery_events d LEFT JOIN delivery_event_actions a ON a.id=(
                    SELECT MAX(id) FROM delivery_event_actions WHERE event_id=d.id)
            ), ranked AS (
                SELECT id,project_id,occurred_at,
                    ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY julianday(occurred_at),id) AS position
                FROM effective WHERE voided=0
            ), firsts AS (SELECT id,project_id,occurred_at FROM ranked WHERE position=1)
            SELECT e.*,f.occurred_at AS first_at,
                CASE WHEN e.voided=1 THEN 'voided' WHEN e.id=f.id THEN 'first' ELSE 'repeat' END AS counting_role
            FROM effective e LEFT JOIN firsts f ON f.project_id=e.project_id''' + where +
                ' ORDER BY julianday(e.occurred_at) DESC,e.id DESC', parameters).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event['voided'] = bool(event['voided'])
            event['counted'] = event['counting_role'] == 'first'
            events.append(event)
        return dict(day=current, project_count=sum(event['counted'] for event in events),
            event_count=len(events), repeat_count=sum(event['counting_role'] == 'repeat' for event in events),
            voided_count=sum(event['voided'] for event in events),
            valid_event_count=sum(not event['voided'] for event in events), events=events)

    def set_delivery_events_voided(self, event_ids, *, voided: bool, confirmed: bool = False, reason: str = '') -> dict:
        """撤销统计与退回待交付同事务提交；恢复只恢复统计，保留原事件和归档。"""
        if not isinstance(event_ids, (list, tuple, set)) or not 1 <= len(event_ids) <= 500:
            raise ValueError('请选择 1 至 500 条交付记录。')
        if any(type(value) is not int or value <= 0 for value in event_ids):
            raise ValueError('交付记录编号无效。')
        if type(voided) is not bool or type(confirmed) is not bool:
            raise ValueError('撤销和确认标记必须是是/否。')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('交付管理原因不能超过 1000 个字符。')
        selected = sorted(set(event_ids))
        result = dict(changed_count=0, event_ids=selected, changed_event_ids=[], reset_project_ids=[],
                      voided=voided, confirmed=confirmed)
        if not confirmed:
            return result
        with self.connect() as connection:
            # 获取写锁后读取当前状态，使并发重复操作也只追加一次。
            connection.execute('BEGIN IMMEDIATE')
            placeholders = ','.join('?' for _ in selected)
            existing = {row['id']: row for row in connection.execute(f'''
                SELECT e.id,e.project_id,COALESCE((SELECT a.voided FROM delivery_event_actions a
                    WHERE a.event_id=e.id ORDER BY a.id DESC LIMIT 1),0) AS voided
                FROM delivery_events e WHERE e.id IN ({placeholders})''', selected)}
            if len(existing) != len(selected):
                raise ValueError('所选交付记录不存在，未修改任何记录。')
            for event_id in selected:
                if bool(existing[event_id]['voided']) == voided:
                    continue
                connection.execute('INSERT INTO delivery_event_actions(event_id,voided,reason) VALUES (?,?,?)',
                    (event_id, int(voided), reason.strip()))
                result['changed_event_ids'].append(event_id)
            if voided:
                # 只处理本次真正撤销的事件，重复撤销不能覆盖其后发生的新交付。
                project_ids = sorted({existing[eid]['project_id'] for eid in result['changed_event_ids']})
                for project_id in project_ids:
                    changed = connection.execute('''
                        UPDATE projects SET status='待交付',delivered_at=NULL,delivered_zip_id=NULL,
                            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                        WHERE id=? AND (status!='待交付' OR delivered_at IS NOT NULL OR delivered_zip_id IS NOT NULL)
                    ''', (project_id,)).rowcount
                    if changed:
                        result['reset_project_ids'].append(project_id)
        result['changed_count'] = len(result['changed_event_ids'])
        return result

    def void_delivery_events(self, event_ids, *, confirmed: bool = False, reason: str = '') -> dict:
        return self.set_delivery_events_voided(event_ids, voided=True, confirmed=confirmed, reason=reason)

    def restore_delivery_events(self, event_ids, *, confirmed: bool = False, reason: str = '') -> dict:
        return self.set_delivery_events_voided(event_ids, voided=False, confirmed=confirmed, reason=reason)

    @staticmethod
    def _append_delivery_event(connection, project_id, kind, archive_path, *, zip_id=None, archive_signature=None):
        project = connection.execute('''SELECT p.name,b.name AS batch_name,p.delivered_at
            FROM projects p JOIN batches b ON b.id=p.batch_id WHERE p.id=?''', (project_id,)).fetchone()
        if project is None or project['delivered_at'] is None:
            raise ValueError('交付时间尚未保存，不能记录交付事实。')
        return connection.execute('''INSERT INTO delivery_events(
            project_id,project_name,batch_name,kind,zip_id,archive_path,archive_signature,occurred_at)
            VALUES (?,?,?,?,?,?,?,?)''', (project_id, project['name'], project['batch_name'], kind,
            zip_id, str(archive_path), archive_signature, project['delivered_at'])).lastrowid

    def _complete_file_delivery(self, connection, project_id: int, verified_archive: dict) -> int:
        """调用方复制归档后，复核清单及副本，再共用其事务保存人工交付事实。"""
        from native_files import ZipLease, identity, plain_path

        if type(project_id) is not int or project_id <= 0 or not isinstance(verified_archive, dict):
            raise ValueError('请提供项目及已经验证的文件归档清单。')
        self._assert_projects_editable(connection, project_id)
        root_value, signature = verified_archive.get('path'), verified_archive.get('signature')
        manifest, file_count = verified_archive.get('manifest'), verified_archive.get('file_count')
        if (not isinstance(root_value, (str, Path)) or not Path(root_value).is_absolute()
                or not isinstance(signature, str) or not re.fullmatch('[0-9a-fA-F]{64}', signature)
                or not isinstance(manifest, list) or not manifest
                or type(file_count) is not int or file_count != len(manifest)):
            raise ValueError('文件归档路径、签名或清单不完整。')
        root = plain_path(root_value)
        if not root.is_dir():
            raise ValueError('文件归档目录不存在。')
        checked, keys = [], set()
        for item in manifest:
            if not isinstance(item, dict):
                raise ValueError('归档文件清单无效。')
            relative, size, digest = item.get('path'), item.get('size'), item.get('sha256')
            if (not isinstance(relative, str) or not relative or '\\' in relative
                    or PurePosixPath(relative).is_absolute() or any(part in ('', '.', '..') for part in relative.split('/'))
                    or type(size) is not int or size < 0
                    or not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest)):
                raise ValueError('归档文件路径、大小或散列无效。')
            target = plain_path(root.joinpath(*PurePosixPath(relative).parts))
            key = relative.casefold()
            if not target.is_relative_to(root) or key in keys:
                raise ValueError('归档文件超出目录或清单包含重复路径。')
            keys.add(key)
            checked.append((target, size, digest.casefold()))
        actual = set()
        for path in root.rglob('*'):
            path = plain_path(path)
            if path.is_file():
                actual.add(path.relative_to(root).as_posix().casefold())
            elif not path.is_dir():
                raise ValueError('归档中存在未验证的特殊文件。')
        if actual != keys:
            raise ValueError('归档目录实际文件与已验证清单不一致。')
        with ExitStack() as leases:
            for path, size, digest in checked:
                lease = leases.enter_context(ZipLease(path, movable=False))
                if lease.initial.st_size != size:
                    raise ValueError('归档文件大小已变化，未标记交付。')
                hasher = hashlib.sha256()
                for block in iter(lambda: lease.stream.read(1024 * 1024), b''):
                    hasher.update(block)
                if hasher.hexdigest() != digest or identity(os.fstat(lease.stream.fileno())) != identity(lease.initial):
                    raise ValueError('归档文件散列已变化，未标记交付。')
            project = connection.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            previous = connection.execute('SELECT * FROM delivery_events WHERE project_id=? ORDER BY id DESC LIMIT 1', (project_id,)).fetchone()
            signature = signature.casefold()
            if (project['status'] == '已交付' and project['delivered_zip_id'] is None and project['delivered_at']
                    and previous is not None and previous['kind'] == 'manual-files'
                    and previous['archive_path'] == str(root) and previous['archive_signature'] == signature):
                return previous['id']
            connection.execute('''UPDATE projects SET status='已交付',delivered_zip_id=NULL,
                delivered_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?''', (project_id,))
            return self._append_delivery_event(connection, project_id, 'manual-files', root, archive_signature=signature)

    def create_capture(
        self, raw_text: str, batch_name: str, projects: list[tuple[str, bool]]
    ) -> dict:
        if not isinstance(raw_text, str) or not raw_text.strip() or len(raw_text) > 200_000:
            raise ValueError("请填写项目消息（最多 200,000 个字符）。")
        if not isinstance(batch_name, str) or not batch_name.strip() or len(batch_name.strip()) > 100:
            raise ValueError("请填写批次名称（最多 100 个字符）。")
        if not projects or len(projects) > 500:
            raise ValueError("请在预览中保留 1 至 500 个项目。")
        cleaned: dict[str, tuple[str, bool]] = {}
        for name, urgent in projects:
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
                raise ValueError("项目名称不能为空，且不能超过 200 个字符。")
            if type(urgent) is not bool:
                raise ValueError("加急标记必须是是/否。")
            name = name.strip()
            key = name.casefold()
            cleaned[key] = (name, urgent or cleaned.get(key, (name, False))[1])
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO batches(name) VALUES (?)", (batch_name.strip(),))
            batch_id = connection.execute("SELECT id FROM batches WHERE name=?", (batch_name.strip(),)).fetchone()[0]
            if connection.execute(
                "SELECT 1 FROM messages WHERE batch_id=? AND raw_text=?", (batch_id, raw_text)
            ).fetchone():
                raise ValueError("该消息在此批次中已收录，请勿重复提交。")
            message_id = connection.execute(
                "INSERT INTO messages(batch_id,raw_text) VALUES (?,?)", (batch_id, raw_text)
            ).lastrowid
            project_ids = []
            for name, urgent in cleaned.values():
                project_ids.append(connection.execute(
                    "INSERT INTO projects(message_id,batch_id,name,urgent) VALUES (?,?,?,?)",
                    (message_id, batch_id, name, int(urgent)),
                ).lastrowid)
            return {"message_id": message_id, "batch_id": batch_id, "project_ids": project_ids}

    def register_zip(self, metadata: dict, project_id: int | None) -> tuple[dict, bool]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_projects_editable(connection, project_id)
            existing = connection.execute(
                "SELECT * FROM zips WHERE path=? COLLATE NOCASE AND sha256=?",
                (metadata["path"], metadata["sha256"]),
            ).fetchone()
            if existing:
                return dict(existing), True
            if project_id is not None and not connection.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone():
                raise KeyError("待关联的项目不存在。")
            zip_id = connection.execute("""
                INSERT INTO zips(project_id,path,filename,size,sha256,mtime_ns)
                VALUES (?,?,?,?,?,?)
            """, (project_id, metadata["path"], metadata["filename"], metadata["size"],
                  metadata["sha256"], metadata["mtime_ns"])).lastrowid
            if project_id is not None:
                connection.execute("""
                    UPDATE projects SET status='待交付',
                        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id=? AND status!='已交付'
                """, (project_id,))
            return dict(connection.execute("SELECT * FROM zips WHERE id=?", (zip_id,)).fetchone()), False

    def list_zips(self, query: str = "", unmatched_only: bool = False) -> list[dict]:
        sql = """SELECT z.*,
            (SELECT group_concat(name,'、') FROM (SELECT p.name FROM projects p
                JOIN zip_project_links l ON l.project_id=p.id WHERE l.zip_id=z.id ORDER BY p.id)) AS project_name,
            (SELECT group_concat(name,'、') FROM (SELECT DISTINCT b.name FROM batches b
                JOIN projects p ON p.batch_id=b.id JOIN zip_project_links l ON l.project_id=p.id
                WHERE l.zip_id=z.id ORDER BY b.id)) AS batch_name
            FROM zips z WHERE 1=1"""
        parameters = []
        if query.strip():
            sql += """ AND (instr(lower(z.filename),lower(?))>0
                OR EXISTS (SELECT 1 FROM zip_project_links l JOIN projects p ON p.id=l.project_id
                    JOIN batches b ON b.id=p.batch_id WHERE l.zip_id=z.id
                    AND (instr(lower(p.name),lower(?))>0 OR instr(lower(b.name),lower(?))>0)))"""
            parameters.extend([query.strip()] * 3)
        if unmatched_only:
            sql += " AND NOT EXISTS (SELECT 1 FROM zip_project_links l WHERE l.zip_id=z.id)"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql + " ORDER BY z.id DESC", parameters)]

    def get_zip(self, zip_id: int) -> dict:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM zips WHERE id=?", (zip_id,)).fetchone()
            if row is None:
                raise KeyError("ZIP 记录不存在。")
            return dict(row)

    def zip_project_ids(self, zip_id: int) -> list[int]:
        with self.connect() as connection:
            return [row[0] for row in connection.execute(
                'SELECT project_id FROM zip_project_links WHERE zip_id=? ORDER BY project_id', (zip_id,))]

    def zip_belongs_to_project(self, zip_id: int, project_id: int) -> bool:
        with self.connect() as connection:
            return connection.execute('SELECT 1 FROM zip_project_links WHERE zip_id=? AND project_id=?',
                                      (zip_id, project_id)).fetchone() is not None

    def get_zip_file_counts(self, zip_id: int) -> list[dict]:
        """返回实际收录时的自动判断事实；旧 ZIP 没有记录时返回空列表。"""
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('''
                SELECT f.*,p.name AS project_name FROM zip_project_file_counts f
                JOIN projects p ON p.id=f.project_id WHERE f.zip_id=? ORDER BY f.project_id
            ''', (zip_id,))]

    def _mark_work_file_pending(
        self, connection: sqlite3.Connection, project_id: int, work_file_id: int,
        duplicate: bool = False,
    ) -> None:
        """与工作文件及收录日志共用调用方事务，成功复用也退回待交付。"""
        if type(project_id) is not int or project_id <= 0 or type(work_file_id) is not int or work_file_id <= 0:
            raise ValueError('工作文件及项目编号无效。')
        if type(duplicate) is not bool:
            raise ValueError('重复收录标记必须为是或否。')
        self._assert_projects_editable(connection, project_id)
        if not connection.execute('SELECT 1 FROM work_files WHERE id=? AND project_id=?',
                                  (work_file_id, project_id)).fetchone():
            raise ValueError('工作文件尚未收录到指定项目。')
        connection.execute('''
            UPDATE projects SET status='待交付',delivered_at=NULL,delivered_zip_id=NULL,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id=? AND (status!='待交付' OR delivered_at IS NOT NULL OR delivered_zip_id IS NOT NULL)
        ''', (project_id,))

    def _apply_zip_file_counts(
        self, connection: sqlite3.Connection, zip_id: int, counts: dict[int, int],
    ) -> None:
        """在收录事务中按每个项目实际文件数判断状态，不修改人工可用版。"""
        if type(zip_id) is not int or zip_id <= 0:
            raise ValueError('ZIP 编号无效。')
        if (not isinstance(counts, dict) or not counts
                or any(type(pid) is not int or pid <= 0 or type(count) is not int or count < 0
                       for pid, count in counts.items())):
            raise ValueError('项目文件数量必须是非负整数。')
        record = connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone()
        if record is None:
            raise KeyError('ZIP 记录不存在。')
        if connection.execute("SELECT 1 FROM zip_bundles WHERE zip_id=? AND match_state='needs-review'", (zip_id,)).fetchone():
            raise ValueError('组合包尚待核对，不能自动判断交付状态。')
        linked = {row[0] for row in connection.execute(
            'SELECT project_id FROM zip_project_links WHERE zip_id=?', (zip_id,))}
        if set(counts) != linked:
            raise ValueError('文件数量必须覆盖 ZIP 的全部关联项目。')
        self._assert_projects_editable(connection, *linked)
        # 验证全部项目后再写入；判断事实与项目状态由调用方一次提交。
        marks = ','.join('?' for _ in linked)
        connection.execute(f'DELETE FROM zip_project_file_counts WHERE zip_id=? AND project_id NOT IN ({marks})',
                           [zip_id, *sorted(linked)])
        for pid, count in sorted(counts.items()):
            status = '已交付' if count > 3 else '待交付'
            connection.execute('''
                INSERT INTO zip_project_file_counts(zip_id,project_id,file_count,decided_status,rule)
                VALUES (?,?,?,?,'zip-file-count-v1')
                ON CONFLICT(zip_id,project_id) DO UPDATE SET file_count=excluded.file_count,
                    decided_status=excluded.decided_status,rule=excluded.rule,
                    checked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE file_count!=excluded.file_count OR decided_status!=excluded.decided_status
                    OR rule!=excluded.rule
            ''', (zip_id, pid, count, status))
            if count > 3:
                changed = connection.execute('''
                    UPDATE projects SET status='已交付',delivered_zip_id=?,
                        delivered_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id=? AND (status!='已交付' OR delivered_zip_id IS NOT ? OR delivered_at IS NULL)
                ''', (zip_id, pid, zip_id)).rowcount
                if changed:
                    self._append_delivery_event(connection, pid, 'auto-zip', record['path'], zip_id=zip_id)
            else:
                connection.execute('''
                    UPDATE projects SET status='待交付',delivered_at=NULL,delivered_zip_id=NULL,
                        updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE id=? AND (status!='待交付' OR delivered_at IS NOT NULL OR delivered_zip_id IS NOT NULL)
                ''', (pid,))

    def get_zip_bundle(self, zip_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM zip_bundles WHERE zip_id=?', (zip_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result.update(is_bundle=True, state=row['match_state'], member_names=json.loads(row['member_names']))
            result['project_ids'] = [item[0] for item in connection.execute(
                'SELECT project_id FROM zip_project_links WHERE zip_id=? ORDER BY project_id', (zip_id,))]
            return result

    def apply_zip_bundle(self, zip_id: int, plan: dict) -> dict:
        with getattr(self, '_operation_lock', nullcontext()), self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            return self._apply_zip_bundle(connection, zip_id, plan)

    def _apply_zip_bundle(self, connection: sqlite3.Connection, zip_id: int, plan: dict) -> dict:
        record = connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone()
        if record is None:
            raise KeyError('ZIP 记录不存在。')
        state = plan.get('state')
        names, count = plan.get('member_names'), plan.get('declared_count')
        if plan.get('is_bundle') is not True or state not in ('matched', 'needs-review'):
            raise ValueError('无效的组合包识别结果。')
        if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError('组合包项目名称无效。')
        if count is not None and (type(count) is not int or count <= 0):
            raise ValueError('组合包声明的项目数量无效。')
        if state == 'matched':
            project_ids, matches = plan.get('project_ids'), plan.get('matches')
            if (not isinstance(project_ids, list) or not project_ids
                    or any(type(pid) is not int or pid <= 0 for pid in project_ids)
                    or len(set(project_ids)) != len(project_ids)
                    or count != len(project_ids) or len(names) != count
                    or len(set(names)) != len(names)
                    or not isinstance(matches, list) or len(matches) != count):
                raise ValueError('组合包项目数量或关联结果不一致，请重新识别。')
            if (any(not isinstance(match, dict) for match in matches)
                    or sorted(match.get('project_id', -1) for match in matches) != sorted(project_ids)
                    or sorted(match.get('folder_name', '') for match in matches) != sorted(names)):
                raise ValueError('组合包名称与关联结果不一致，请重新识别。')
            self._assert_projects_editable(connection, *project_ids)
            batch_ids = set()
            for match in matches:
                project = connection.execute('SELECT name,batch_id FROM projects WHERE id=?', (match['project_id'],)).fetchone()
                if project['name'] != match.get('project_name') or project['batch_id'] != match.get('batch_id'):
                    raise ValueError('项目名称或批次已变化，请重新识别组合包。')
                batch_ids.add(project['batch_id'])
            if len(batch_ids) != 1:
                raise ValueError('组合包项目必须来自同一批次。')
            confirmed = {row[0] for row in connection.execute('SELECT id FROM projects WHERE delivered_zip_id=?', (zip_id,))}
            if not confirmed.issubset(project_ids):
                raise ValueError('组合包包含已确认交付的项目，不能移除其关联。')
            # 全部校验通过后再写入，调用者已有事务时也不会留下半组关联。
            connection.execute('DELETE FROM zip_bundle_members WHERE zip_id=?', (zip_id,))
            connection.executemany('INSERT INTO zip_bundle_members(zip_id,project_id) VALUES (?,?)',
                                   [(zip_id, pid) for pid in project_ids])
            primary = record['project_id'] if record['project_id'] in project_ids else project_ids[0]
            connection.execute('UPDATE zips SET project_id=? WHERE id=?', (primary, zip_id))
            marks = ','.join('?' for _ in project_ids)
            connection.execute(f"UPDATE projects SET status='待交付',updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id IN ({marks}) AND status!='已交付'", project_ids)
            connection.execute(f'DELETE FROM zip_project_approvals WHERE zip_id=? AND project_id NOT IN ({marks})', [zip_id, *project_ids])
        connection.execute('''INSERT INTO zip_bundles(zip_id,declared_count,member_names,match_state,reason)
            VALUES (?,?,?,?,?) ON CONFLICT(zip_id) DO UPDATE SET declared_count=excluded.declared_count,
            member_names=excluded.member_names,match_state=excluded.match_state,reason=excluded.reason''',
            (zip_id, count, json.dumps(names, ensure_ascii=False), state, str(plan.get('reason') or '')))
        return dict(connection.execute('SELECT * FROM zips WHERE id=?', (zip_id,)).fetchone())

    def migrate_zip_approvals(self, connection: sqlite3.Connection) -> None:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='zip_snapshots'").fetchone():
            return
        if connection.execute("SELECT 1 FROM zip_approval_migrations WHERE name='snapshot-approved-v1'").fetchone():
            return
        connection.execute('''INSERT OR IGNORE INTO zip_project_approvals(project_id,zip_id)
            SELECT l.project_id,max(l.zip_id) FROM zip_project_links l
            JOIN zip_snapshots s ON s.zip_id=l.zip_id WHERE s.approved=1 GROUP BY l.project_id''')
        connection.execute("INSERT INTO zip_approval_migrations(name) VALUES ('snapshot-approved-v1')")

    def _approve_zip_for_projects(self, connection: sqlite3.Connection, zip_id: int) -> None:
        if connection.execute("SELECT 1 FROM zip_bundles WHERE zip_id=? AND match_state='needs-review'", (zip_id,)).fetchone():
            raise ValueError('组合包尚待核对，请完整匹配项目后再标记可用版本。')
        project_ids = [row[0] for row in connection.execute('SELECT project_id FROM zip_project_links WHERE zip_id=?', (zip_id,))]
        if not project_ids:
            raise ValueError('请先关联项目，再标记可用版本。')
        self._assert_projects_editable(connection, *project_ids)
        connection.executemany('''INSERT INTO zip_project_approvals(project_id,zip_id) VALUES (?,?)
            ON CONFLICT(project_id) DO UPDATE SET zip_id=excluded.zip_id''', [(pid, zip_id) for pid in project_ids])

    def approved_zip_for_project(self, project_id: int) -> int | None:
        with self.connect() as connection:
            row = connection.execute('''SELECT a.zip_id FROM zip_project_approvals a JOIN zip_project_links l
                ON l.zip_id=a.zip_id AND l.project_id=a.project_id WHERE a.project_id=?
                AND NOT EXISTS (SELECT 1 FROM zip_bundles b WHERE b.zip_id=a.zip_id AND b.match_state='needs-review')''', (project_id,)).fetchone()
            return row[0] if row else None

    def link_zip(self, zip_id: int, project_id: int) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM zips WHERE id=?", (zip_id,)).fetchone()
            if row is None or not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError("项目或 ZIP 记录不存在。")
            if connection.execute('SELECT COUNT(*) FROM zip_project_links WHERE zip_id=?', (zip_id,)).fetchone()[0] > 1:
                raise ValueError('组合包关联多个项目，不能单独改关联；请按组合包整体重新识别。')
            if connection.execute('SELECT 1 FROM zip_bundles WHERE zip_id=? AND declared_count>1', (zip_id,)).fetchone():
                raise ValueError('组合包声明包含多个项目，不能单独改关联；请按组合包整体重新识别。')
            self._assert_projects_editable(connection, row['project_id'], project_id)
            if row["project_id"] == project_id:
                return
            if connection.execute("SELECT 1 FROM projects WHERE delivered_zip_id=?", (zip_id,)).fetchone():
                raise ValueError("该 ZIP 已用于确认交付，不能重新关联；请先将原项目退回开发状态。")
            connection.execute("UPDATE zips SET project_id=? WHERE id=?", (project_id, zip_id))
            connection.execute('DELETE FROM zip_bundle_members WHERE zip_id=?', (zip_id,))
            connection.execute('DELETE FROM zip_bundles WHERE zip_id=?', (zip_id,))
            connection.execute('DELETE FROM zip_project_approvals WHERE zip_id=?', (zip_id,))
            connection.execute("""
                UPDATE projects SET status='待交付', updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE id=? AND status!='已交付'
            """, (project_id,))

    def complete_delivery(self, project_id: int, zip_id: int, verified: dict) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            record = connection.execute("SELECT * FROM zips WHERE id=?", (zip_id,)).fetchone()
            if project is None or record is None:
                raise KeyError("项目或 ZIP 记录不存在。")
            if connection.execute("SELECT 1 FROM zip_bundles WHERE zip_id=? AND match_state='needs-review'", (zip_id,)).fetchone():
                raise ValueError('组合包尚待核对，请完整匹配项目后再确认交付。')
            self._assert_projects_editable(connection, project_id)
            linked = connection.execute('SELECT 1 FROM zip_project_links WHERE zip_id=? AND project_id=?', (zip_id, project_id)).fetchone()
            if project["status"] != "待交付" or not linked:
                raise ValueError("项目状态或 ZIP 关联已变化，请刷新后重新确认。")
            if any(record[key] != verified[key] for key in ("path", "size", "sha256")):
                raise ValueError("ZIP 元数据已变化，无法确认交付。")
            connection.execute("""
                UPDATE projects SET status='已交付', delivered_zip_id=?,
                    delivered_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?
            """, (zip_id, project_id))
            self._append_delivery_event(connection, project_id, 'manual-zip', record['path'], zip_id=zip_id)

    def list_projects(
        self, query: str = "", status: str | None = None,
        batch_id: int | None = None, urgent_only: bool = False,
        *, archived: bool | None = False, due_filter: str | None = None,
        today: date | None = None,
    ) -> list[dict]:
        clauses = ["1=1"]
        parameters: list = []
        if query.strip():
            clauses.append("""(instr(lower(p.name),lower(?)) > 0
                OR instr(lower(b.name),lower(?)) > 0
                OR instr(lower(COALESCE(d.notes,'')),lower(?)) > 0
                OR EXISTS (SELECT 1 FROM zips z JOIN zip_project_links l ON l.zip_id=z.id WHERE l.project_id=p.id
                    AND instr(lower(z.filename),lower(?)) > 0))""")
            parameters.extend([query.strip()] * 4)
        if archived is not None and type(archived) is not bool:
            raise ValueError('归档筛选必须为是、否或全部。')
        if archived is not None:
            clauses.append('d.archived_at IS NOT NULL' if archived else 'd.archived_at IS NULL')
        if due_filter not in (None, 'overdue', 'today', 'week', 'unscheduled'):
            raise ValueError('无效的截止日期筛选。')
        if today is not None and type(today) is not date:
            raise ValueError('基准日期必须是日期。')
        if due_filter is not None:
            current = today or date.today()
            clauses.append("p.status!='已交付'")
            if due_filter == 'unscheduled':
                clauses.append('d.due_date IS NULL')
            elif due_filter == 'overdue':
                clauses.append('d.due_date < ?')
                parameters.append(current.isoformat())
            elif due_filter == 'today':
                clauses.append('d.due_date = ?')
                parameters.append(current.isoformat())
            else:
                clauses.append('d.due_date BETWEEN ? AND ?')
                parameters.extend((current.isoformat(), (current + timedelta(days=6)).isoformat()))
        if status is not None:
            if status not in STATUSES:
                raise ValueError("无效的项目状态。")
            clauses.append("p.status=?")
            parameters.append(status)
        if batch_id is not None:
            clauses.append("p.batch_id=?")
            parameters.append(batch_id)
        if urgent_only:
            clauses.append("p.urgent=1")
        sql = """SELECT p.*, b.name AS batch_name, COALESCE(d.notes,'') AS notes,
            d.due_date, d.archived_at,
            (SELECT COUNT(*) FROM zip_project_links l WHERE l.project_id=p.id) AS zip_count,
            (SELECT filename FROM zips z JOIN zip_project_links l ON l.zip_id=z.id WHERE l.project_id=p.id ORDER BY z.id DESC LIMIT 1) AS zip_name
            FROM projects p JOIN batches b ON b.id=p.batch_id
            LEFT JOIN project_details d ON d.project_id=p.id WHERE """
        sql += " AND ".join(clauses) + " ORDER BY p.urgent DESC, d.due_date IS NULL, d.due_date ASC, p.id DESC"
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters)]

    def list_batches(self) -> list[dict]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("""
                SELECT b.*, COUNT(p.id) AS project_count,
                    COALESCE(SUM(p.status='已交付'),0) AS delivered_count
                FROM batches b LEFT JOIN projects p ON p.batch_id=b.id
                GROUP BY b.id ORDER BY b.id DESC
            """)]

    def get_project(self, project_id: int) -> dict:
        with self.connect() as connection:
            row = connection.execute("""
                SELECT p.*, b.name AS batch_name, m.raw_text,
                    COALESCE(d.notes,'') AS notes, d.due_date, d.archived_at FROM projects p
                JOIN batches b ON b.id=p.batch_id JOIN messages m ON m.id=p.message_id
                LEFT JOIN project_details d ON d.project_id=p.id
                WHERE p.id=?
            """, (project_id,)).fetchone()
            if row is None:
                raise KeyError("项目不存在，请刷新列表。")
            result = dict(row)
            result["zips"] = [dict(item) for item in connection.execute(
                "SELECT z.* FROM zips z JOIN zip_project_links l ON l.zip_id=z.id WHERE l.project_id=? ORDER BY z.id DESC", (project_id,)
            )]
            return result

    def set_status(self, project_id: int, status: str) -> None:
        self.bulk_update_projects([project_id], status=status)

    def set_urgent(self, project_id: int, urgent: bool) -> None:
        self.bulk_update_projects([project_id], urgent=urgent)

    def update_project_details(self, project_id: int, notes: str, due_date: str | None) -> None:
        if not isinstance(notes, str) or len(notes) > 10000:
            raise ValueError('备注必须是文本，且不能超过 10,000 个字符。')
        if due_date == '':
            due_date = None
        if due_date is not None:
            if not isinstance(due_date, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', due_date):
                raise ValueError('截止日期需要 YYYY-MM-DD 格式。')
            try:
                date.fromisoformat(due_date)
            except ValueError as error:
                raise ValueError('截止日期不是有效日期。') from error
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT p.id,d.archived_at FROM projects p LEFT JOIN project_details d ON d.project_id=p.id WHERE p.id=?', (project_id,)).fetchone()
            if row is None:
                raise KeyError('项目不存在，请刷新列表。')
            if row['archived_at'] is not None:
                raise ValueError('请先恢复归档项目，再编辑。')
            connection.execute('INSERT INTO project_details(project_id,notes,due_date) VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET notes=excluded.notes,due_date=excluded.due_date', (project_id, notes, due_date))
            connection.execute("UPDATE projects SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (project_id,))

    def bulk_update_projects(self, project_ids, *, status=None, urgent=None, archived=None) -> None:
        if not isinstance(project_ids, (list, tuple)) or not 1 <= len(project_ids) <= 500 or any(type(pid) is not int or pid <= 0 for pid in project_ids) or len(set(project_ids)) != len(project_ids):
            raise ValueError('请选择 1 至 500 个不同的有效项目。')
        if status is not None and status not in STATUSES[:3]:
            raise ValueError('仅可批量设置待开发、开发中或待交付；已交付由项目ZIP文件数判断，也可手动确认。')
        if urgent is not None and type(urgent) is not bool:
            raise ValueError("加急标记必须是是/否。")
        if archived is not None and type(archived) is not bool:
            raise ValueError('归档标记必须是是/否。')
        if archived is not None and (status is not None or urgent is not None):
            raise ValueError('归档和恢复不能与状态或加急更新同时执行。')
        if status is None and urgent is None and archived is None:
            raise ValueError('请选择需要更新的内容。')
        # 托管归档共享此锁：先结束文件操作，再改变项目归档边界。
        with getattr(self, '_operation_lock', nullcontext()), self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            marks = ','.join('?' for _ in project_ids)
            rows = connection.execute(f'SELECT p.id,d.archived_at FROM projects p LEFT JOIN project_details d ON d.project_id=p.id WHERE p.id IN ({marks})', project_ids).fetchall()
            if len(rows) != len(project_ids):
                raise KeyError('部分项目不存在，请刷新列表。')
            if archived is None and any(row['archived_at'] is not None for row in rows):
                raise ValueError('请先恢复归档项目，再编辑。')
            if archived is not None:
                for pid in project_ids:
                    connection.execute('INSERT OR IGNORE INTO project_details(project_id) VALUES (?)', (pid,))
                value = "COALESCE(archived_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))" if archived else 'NULL'
                connection.execute(f'UPDATE project_details SET archived_at={value} WHERE project_id IN ({marks})', project_ids)
            else:
                changes, values = [], []
                if status is not None:
                    changes.extend(('status=?', 'delivered_at=NULL', 'delivered_zip_id=NULL'))
                    values.append(status)
                if urgent is not None:
                    changes.append('urgent=?')
                    values.append(int(urgent))
                changes.append("updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')")
                connection.execute(f'UPDATE projects SET {",".join(changes)} WHERE id IN ({marks})', [*values, *project_ids])

    @staticmethod
    def _assert_projects_editable(connection, *project_ids):
        for project_id in set(project_ids) - {None}:
            row = connection.execute('SELECT p.id,d.archived_at FROM projects p LEFT JOIN project_details d ON d.project_id=p.id WHERE p.id=?', (project_id,)).fetchone()
            if row is None:
                raise KeyError('项目不存在，请刷新列表。')
            if row['archived_at'] is not None:
                raise ValueError('请先恢复归档项目，再编辑或交付。')
