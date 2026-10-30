"""Application service independent of Qt; usable from tests and the GUI."""

from dataclasses import asdict
from pathlib import Path
from concurrent.futures import CancelledError

from database import Database
from parser import ParsedProject, parse_message
from zip_manager import inspect_zip, match_projects

VERSION = "1.0.0"


class WorkBox:
    def __init__(self, database_path: str | Path):
        self.db = Database(database_path)

    def capture_message(
        self, raw_text: str, batch_name: str, projects: list[ParsedProject] | None = None
    ) -> dict:
        candidates = parse_message(raw_text) if projects is None else projects
        if not all(isinstance(project, ParsedProject) for project in candidates):
            raise ValueError("无效的项目预览。")
        return self.db.create_capture(
            raw_text, batch_name, [(project.name, project.urgent) for project in candidates]
        )

    def list_projects(
        self, query: str = "", status: str | None = None,
        batch_id: int | None = None, urgent_only: bool = False,
        *, archived: bool | None = False, due_filter: str | None = None, today=None,
        deleted: bool | None = False, type_id: int | None = None,
    ) -> list[dict]:
        return self.db.list_projects(query, status, batch_id, urgent_only,
                                     archived=archived, due_filter=due_filter, today=today,
                                     deleted=deleted, type_id=type_id)

    def set_project_delivery_counting(self, project_ids, included: bool, *, confirmed: bool = False,
                                      reason: str = '') -> dict:
        return self.db.set_project_delivery_counting(project_ids, included, confirmed=confirmed, reason=reason)

    def list_project_types(self, *, include_inactive: bool = False) -> list[dict]:
        return self.db.list_project_types(include_inactive=include_inactive)

    def get_default_project_type(self) -> int | None:
        return self.db.get_default_project_type()

    def set_default_project_type(self, type_id: int | None) -> int | None:
        return self.db.set_default_project_type(type_id)

    def create_project_type(self, name: str) -> dict:
        return self.db.create_project_type(name)

    def rename_project_type(self, type_id: int, name: str) -> dict:
        return self.db.rename_project_type(type_id, name)

    def set_project_type_active(self, type_id: int, active: bool) -> dict:
        return self.db.set_project_type_active(type_id, active)

    def set_project_types(self, project_ids, type_id: int | None) -> dict:
        return self.db.set_project_types(project_ids, type_id)

    def delete_project(self, project_id: int, *, confirmed: bool = False) -> dict:
        return self.db.delete_project(project_id, confirmed=confirmed)

    def restore_deleted_project(self, project_id: int) -> dict:
        return self.db.restore_deleted_project(project_id)

    def update_project_details(self, project_id: int, notes: str, due_date: str | None) -> None:
        self.db.update_project_details(project_id, notes, due_date)

    def bulk_update_projects(self, project_ids, *, status=None, urgent=None, archived=None) -> None:
        self.db.bulk_update_projects(project_ids, status=status, urgent=urgent, archived=archived)

    def list_batches(self) -> list[dict]:
        return self.db.list_batches()

    def get_project(self, project_id: int) -> dict:
        return self.db.get_project(project_id)

    def set_status(self, project_id: int, status: str) -> None:
        self.db.set_status(project_id, status)

    def set_urgent(self, project_id: int, urgent: bool) -> None:
        self.db.set_urgent(project_id, urgent)

    def import_zip(self, path: str | Path, *, cancel_event=None, progress=None) -> dict:
        info = inspect_zip(path, cancel_event=cancel_event, progress=progress)
        candidates = match_projects(info.filename, self.list_projects())
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("已取消 ZIP 登记。")
        project_id = candidates[0]["id"] if len(candidates) == 1 else None
        record, duplicate = self.db.register_zip(asdict(info), project_id)
        return {"zip": record, "candidates": candidates, "duplicate": duplicate}

    def list_zips(self, query: str = "", unmatched_only: bool = False) -> list[dict]:
        return self.db.list_zips(query, unmatched_only)

    def link_zip(self, zip_id: int, project_id: int) -> None:
        self.db.link_zip(zip_id, project_id)

    def confirm_delivery(
        self, project_id: int, zip_id: int, *, confirmed: bool = False,
        cancel_event=None, progress=None,
    ) -> dict:
        if confirmed is not True:
            raise ValueError("交付必须由你明确确认。")
        project = self.get_project(project_id)
        if project['archived_at'] is not None:
            raise ValueError('请先恢复归档项目，再确认交付。')
        record = self.db.get_zip(zip_id)
        bundle = self.db.get_zip_bundle(zip_id)
        if bundle is not None and bundle['match_state'] == 'needs-review':
            raise ValueError('组合包尚待核对，请完整匹配项目后再确认交付。')
        if project["status"] != "待交付" or not self.db.zip_belongs_to_project(zip_id, project_id):
            raise ValueError("仅可确认待交付项目下已关联的 ZIP。")
        info = inspect_zip(record["path"], cancel_event=cancel_event, progress=progress)
        if info.size != record["size"] or info.sha256 != record["sha256"]:
            raise ValueError("ZIP 内容已变化，请重新拖入并选择新的记录确认交付。")
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("已取消交付确认。")
        self.db.complete_delivery(project_id, zip_id, asdict(info))
        return self.get_project(project_id)
