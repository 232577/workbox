"""Expose a project's unambiguous file to a native, copy-only drag."""

import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtGui import QDrag

from workbox.desktop.floating import DOCUMENT_DRAG_MIME
from workbox.desktop.project_documents_ui import PREFERRED_DROP_EFFECT
from workbox.projects.archive_facts import material_archive_signature
from workbox.storage.native_files import ZipLease, hash_named_streams, hash_stream
from workbox.storage.project_documents import list_project_documents
from workbox.storage.workspace_manager import _path


def resolve_drag_file(service, project_id):
    with _hold_drag_file(service, project_id) as path:
        return path


@contextmanager
def _hold_drag_file(service, project_id):
    project = service.get_project(project_id)
    if project['status'] == '已交付':
        events = service.db.delivery_history(project_id=project_id)['events']
        delivery = next((event for event in events if not event['voided']), None)
        if delivery is not None:
            with ExitStack() as leases:
                yield _delivered_file(service, project, delivery, leases)
            return

    snapshot = list_project_documents(service, project_id)
    if snapshot['errors']:
        raise ValueError('当前文件未能完整核对，请点项目名查看文件。')
    approved = {row['id'] for row in project['zips'] if row.get('bundle_state') != 'needs-review'}
    files = [_path(row['path']) for row in snapshot['documents']
             if row['kind'] in ('工作文件', 'ZIP工作副本') or row.get('zip_id') in approved]
    yield _only_file(files)


def _delivered_file(service, project, delivery, leases):
    path = _path(delivery['archive_path'])
    if delivery['zip_id'] is not None:
        record = next((row for row in project['zips'] if row['id'] == delivery['zip_id']
                       and Path(row['path']) == path), None)
        return _verified_archive_file(path, record, leases)

    with service.db.connect() as connection:
        archive = connection.execute('''SELECT manifest FROM project_delivery_archives
            WHERE project_id=? AND path=? AND signature=?''',
            (project['id'], str(path), delivery['archive_signature'])).fetchone()
    manifest = json.loads(archive['manifest']) if archive else []
    if len(manifest) > 1:
        raise ValueError('本次交付有多个归档文件，请点项目名选择要取出的文件。')
    if not manifest or material_archive_signature(manifest) != delivery['archive_signature']:
        raise ValueError('归档清单缺失或已变化，请点项目名核对文件。')
    record = manifest[0]
    item = _path(path / record['path'])
    if item == path or not item.is_relative_to(path):
        raise ValueError('归档文件超出登记目录，请点项目名核对文件。')
    return _verified_archive_file(item, record, leases, streams=record.get('streams', {}))


def _verified_archive_file(path, record, leases, *, streams=None):
    if record is None or not path.is_file():
        raise ValueError('找不到本次交付登记的归档，请点项目名核对文件。')
    lease = leases.enter_context(ZipLease(path, movable=False))
    if (os.fstat(lease.stream.fileno()).st_size != record['size']
            or hash_stream(lease.stream) != (record['sha256'], record['size'])):
        raise ValueError('归档内容已变化，请点项目名核对文件。')
    if streams is not None and hash_named_streams(lease, leases) != streams:
        raise ValueError('归档内容已变化，请点项目名核对文件。')
    return path


def _only_file(files):
    if len(files) > 1:
        raise ValueError('项目有多个文件，请点项目名选择要取出的文件。')
    if not files or not files[0].is_file():
        raise ValueError('没有可拖出的文件，请点项目名查看当前文件。')
    return files[0]


def drag_project_file(service, project_id, source):
    with _hold_drag_file(service, project_id) as path:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        mime.setData(DOCUMENT_DRAG_MIME, b'1')
        mime.setData(PREFERRED_DROP_EFFECT, b'\x01\x00\x00\x00')
        drag = QDrag(source)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.CopyAction, Qt.CopyAction)
        finally:
            drag.deleteLater()
