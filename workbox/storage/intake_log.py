"""文件收录结果的轻量事实日志。"""

from __future__ import annotations

import json
from pathlib import Path


STATUSES = ("copied", "duplicate", "failed", "cancelled", "needs-choice")

SCHEMA = """
CREATE TABLE IF NOT EXISTS file_intake_events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    status TEXT NOT NULL CHECK(status IN ('copied','duplicate','failed','cancelled','needs-choice')),
    source_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    target_path TEXT NOT NULL DEFAULT '',
    project_id INTEGER,
    project_name TEXT NOT NULL DEFAULT '',
    basis TEXT NOT NULL DEFAULT '',
    candidate_ids TEXT NOT NULL DEFAULT '[]',
    manual INTEGER CHECK(manual IS NULL OR manual IN (0,1)),
    message TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    work_file_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_file_intake_events_created
    ON file_intake_events(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_file_intake_events_work_file
    ON file_intake_events(work_file_id, status);
"""


class FileIntakeLog:
    """记录收录决策，并兼容展示日志功能上线前的工作文件。"""

    def __init__(self, db):
        self.db = db
        with self.db.connect() as connection:
            connection.executescript(SCHEMA)

    def append(
        self,
        source_path,
        status,
        *,
        project_id=None,
        project_name="",
        target_path="",
        basis="",
        candidate_ids=(),
        manual=None,
        message="",
        sha256="",
        work_file_id=None,
        connection=None,
    ) -> int:
        if status not in STATUSES:
            raise ValueError("无效的文件收录状态。")
        if manual is not None and type(manual) is not bool:
            raise ValueError("人工选择标记必须为是、否或未记录。")
        source_path = str(source_path)
        if not source_path:
            raise ValueError("源文件路径不能为空。")
        candidates = list(candidate_ids)
        if any(type(candidate_id) is not int or candidate_id < 1 for candidate_id in candidates):
            raise ValueError("候选项目编号必须为正整数。")

        values = (
            status,
            source_path,
            Path(source_path).name,
            str(target_path),
            project_id,
            str(project_name),
            str(basis),
            json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
            None if manual is None else int(manual),
            str(message),
            str(sha256),
            work_file_id,
        )
        sql = """
            INSERT INTO file_intake_events(
                status,source_path,filename,target_path,project_id,project_name,
                basis,candidate_ids,manual,message,sha256,work_file_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """
        if connection is not None:
            return connection.execute(sql, values).lastrowid
        with self.db.connect() as own_connection:
            return own_connection.execute(sql, values).lastrowid

    def list_recent(self, query: str = "", limit: int = 200) -> list[dict]:
        if type(query) is not str:
            raise ValueError("搜索内容必须是文本。")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("日志条数必须为 1 至 500。")

        term = query.strip()
        with self.db.connect() as connection:
            has_legacy = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_files'"
            ).fetchone() is not None
            statements = [self._event_select(term)]
            parameters = [term] * 5 if term else []
            if has_legacy:
                statements.append(self._legacy_select(term))
                if term:
                    parameters.extend([term] * 5)
            sql = " UNION ALL ".join(statements)
            rows = connection.execute(
                f"""SELECT * FROM ({sql})
                    ORDER BY created_at DESC, kind_order DESC, raw_id DESC
                    LIMIT ?""",
                [*parameters, limit],
            ).fetchall()
        return [self._as_result(row) for row in rows]

    @staticmethod
    def _event_select(term: str) -> str:
        where = ""
        if term:
            where = """
                WHERE instr(lower(filename),lower(?)) > 0
                   OR instr(lower(source_path),lower(?)) > 0
                   OR instr(lower(target_path),lower(?)) > 0
                   OR instr(lower(project_name),lower(?)) > 0
                   OR instr(lower(basis),lower(?)) > 0
            """
        return f"""
            SELECT id AS raw_id, 'event' AS kind, 1 AS kind_order,
                created_at,status,filename,source_path,target_path,project_id,
                project_name,basis,candidate_ids,manual,message,sha256,work_file_id
            FROM file_intake_events
            {where}
        """

    @staticmethod
    def _legacy_select(term: str) -> str:
        search = ""
        if term:
            search = """
                AND (instr(lower(w.filename),lower(?)) > 0
                  OR instr(lower(w.source_path),lower(?)) > 0
                  OR instr(lower(w.path),lower(?)) > 0
                  OR instr(lower(COALESCE(p.name,'')),lower(?)) > 0
                  OR instr(lower('旧记录未记录判断依据'),lower(?)) > 0)
            """
        return f"""
            SELECT w.id AS raw_id, 'legacy' AS kind, 0 AS kind_order,
                w.created_at AS created_at,
                'historical' AS status,
                w.filename AS filename,
                w.source_path AS source_path,
                w.path AS target_path,
                w.project_id AS project_id,
                COALESCE(p.name,'') AS project_name,
                '旧记录未记录判断依据' AS basis,
                '[]' AS candidate_ids,
                NULL AS manual,
                '' AS message,
                w.sha256 AS sha256,
                w.id AS work_file_id
            FROM work_files w
            LEFT JOIN projects p ON p.id=w.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM file_intake_events e
                WHERE e.work_file_id=w.id AND e.status IN ('copied','duplicate')
            )
            {search}
        """

    @staticmethod
    def _as_result(row) -> dict:
        return {
            "id": f"{row['kind']}:{row['raw_id']}",
            "created_at": row["created_at"],
            "status": row["status"],
            "filename": row["filename"],
            "source_path": row["source_path"],
            "target_path": row["target_path"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "basis": row["basis"],
            "candidate_ids": json.loads(row["candidate_ids"]),
            "manual": None if row["manual"] is None else bool(row["manual"]),
            "message": row["message"],
            "sha256": row["sha256"],
            "work_file_id": row["work_file_id"],
        }
