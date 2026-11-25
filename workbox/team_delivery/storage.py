"""保存原件及跨进程恢复所需的本机交件记录。"""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import file_digest, sha256
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sqlite3
import stat
import tempfile
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
import zlib

from workbox.team_delivery.contracts import Material


_MAX_WORD_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_XML_BYTES = 32 * 1024 * 1024
_WORD_TYPES = 'http://schemas.openxmlformats.org/package/2006/content-types'
_RELATIONSHIPS = 'http://schemas.openxmlformats.org/package/2006/relationships'
_WORD_NAMESPACES = (
    'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'http://purl.oclc.org/ooxml/wordprocessingml/main',
)


class StorageError(ValueError):
    """可以直接向员工显示的本机保存错误。"""


def _read_xml(archive, part):
    if archive.getinfo(part).file_size > _MAX_XML_BYTES:
        raise StorageError('Word 文档的结构部件过大。')
    data = archive.read(part)
    xml = data.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
        raise StorageError('Word 文档包含不支持的 XML 声明。')
    return ElementTree.fromstring(data)


def _validate_word(path):
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename.casefold() for member in members]
            if (len(members) > 4096 or len(set(names)) != len(names)
                    or sum(member.file_size for member in members) > _MAX_ARCHIVE_BYTES):
                raise StorageError('Word 文档的包结构超出支持范围。')
            if any(member.flag_bits & 1 or '\\' in member.filename
                   or PurePosixPath(member.filename).is_absolute() or '..' in PurePosixPath(member.filename).parts
                   for member in members) or 'word/vbaproject.bin' in names:
                raise StorageError('Word 文档包含加密、宏或无效的包路径。')
            types = _read_xml(archive, '[Content_Types].xml')
            rels = _read_xml(archive, '_rels/.rels')
            document = _read_xml(archive, 'word/document.xml')
            if (types.tag != f'{{{_WORD_TYPES}}}Types'
                    or not any(item.get('PartName') == '/word/document.xml'
                               and item.get('ContentType') == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
                               for item in types.findall(f'{{{_WORD_TYPES}}}Override'))
                    or rels.tag != f'{{{_RELATIONSHIPS}}}Relationships'
                    or not any(item.get('Target', '').lstrip('/') == 'word/document.xml'
                               and item.get('Type', '').endswith('/officeDocument')
                               and item.get('TargetMode', 'Internal') == 'Internal'
                               for item in rels.findall(f'{{{_RELATIONSHIPS}}}Relationship'))
                    or not any(document.tag == f'{{{ns}}}document' and document.find(f'{{{ns}}}body') is not None
                               for ns in _WORD_NAMESPACES)
                    or archive.testzip() is not None):
                raise StorageError('文件不是完整的 Word DOCX 文档。')
    except StorageError:
        raise
    except (BadZipFile, LookupError, ElementTree.ParseError, EOFError, RuntimeError, ValueError, zlib.error) as error:
        raise StorageError('文件不是可校验的 Word DOCX 文档。') from error


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _safe_name(name):
    return (name not in ('', '.', '..') and not PureWindowsPath(name).is_reserved()
            and not any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name))


@dataclass(frozen=True)
class StagedWord:
    path: Path
    name: str
    sha256: str


class MaterialStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    @contextmanager
    def stage_word(self, source: Path):
        if source.suffix.casefold() != '.docx' or not _safe_name(source.name) or source.is_symlink():
            raise StorageError('请选择可读取的本地 Word DOCX 原件。')
        if self.root.resolve() != self.root:
            raise StorageError('材料目录发生变化，请核对获准的保存位置。')
        self.root.mkdir(parents=True, exist_ok=True)
        self._require_directory(self.root)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix='.intake-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            try:
                with source.open('rb') as incoming:
                    original = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(original.st_mode) or not 0 < original.st_size <= _MAX_WORD_BYTES:
                        raise StorageError('Word 原件为空、过大或不可作为普通文件读取。')
                    digest = sha256()
                    total = 0
                    while block := incoming.read(1024 * 1024):
                        total += len(block)
                        if total > _MAX_WORD_BYTES:
                            raise StorageError('Word 原件过大。')
                        output.write(block)
                        digest.update(block)
                    if (total != original.st_size or _identity(original) != _identity(os.fstat(incoming.fileno()))
                            or _identity(original) != _identity(source.stat(follow_symlinks=False))):
                        raise StorageError('原件仍在修改，请保存后重新拖入。')
                output.flush()
                os.fsync(output.fileno())
                output.close()
                _validate_word(temporary)
                yield StagedWord(temporary, source.name, digest.hexdigest())
            finally:
                output.close()
                if self.root.resolve() == self.root:
                    temporary.unlink(missing_ok=True)

    def material(self, operation, staged=None):
        if (len(operation.request_id) != 32 or any(char not in '0123456789abcdef' for char in operation.request_id)
                or not _safe_name(operation.name)):
            raise StorageError('本机交件记录中的文件位置无效。')
        task_directory = self._task_directory(operation.task_id)
        directory = task_directory / operation.request_id
        if self.root.resolve() != self.root or task_directory.resolve() != task_directory:
            raise StorageError('材料目录发生变化，请核对获准的保存位置。')
        if staged is not None:
            directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve() != directory:
            raise StorageError('材料目录发生变化，请核对获准的保存位置。')
        target = directory / operation.name
        if staged is not None and not target.exists():
            try:
                # Windows 的 rename 不覆盖目标。其他系统用硬链接实现相同语义。
                if os.name == 'nt':
                    os.rename(staged.path, target)
                else:
                    os.link(staged.path, target)
            except FileExistsError:
                pass
        return self._checked_material(target, operation.sha256)

    def _task_directory(self, task_id: str) -> Path:
        name = task_id
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name) or not _safe_name(name):
            name = 'task-' + sha256(task_id.encode('utf-8')).hexdigest()
        return self.root / name

    def _require_directory(self, directory: Path):
        if directory.resolve(strict=True) != directory or not directory.is_dir():
            raise StorageError('材料目录发生变化，请核对获准的保存位置。')

    def _archive_directory(self, directory: Path) -> None:
        # 分层核对，避免在已有链接或重定向的任务目录下创建子目录。
        self._require_directory(directory.parent)
        if directory.resolve() != directory:
            raise StorageError('材料目录发生变化，请核对获准的保存位置。')
        directory.mkdir(exist_ok=True)
        self._require_directory(directory)

    def archive_word(self, task_id: str, staged: StagedWord) -> Material:
        if not _safe_name(staged.name) or not re.fullmatch(r'[0-9a-f]{64}', staged.sha256):
            raise StorageError('暂存原件的文件名或摘要无效。')
        if staged.path.parent != self.root or staged.path.resolve(strict=True) != staged.path:
            raise StorageError('原件暂存位置已变化，请核对保存路径。')
        task_directory = self._task_directory(task_id)
        self._archive_directory(task_directory)
        key = sha256('\0'.join((task_id, staged.name, staged.sha256)).encode('utf-8')).hexdigest()
        directory = task_directory / ('version-' + key)
        self._archive_directory(directory)
        target = directory / staged.name
        if target.resolve() != target:
            raise StorageError('原件落位发生路径重定向，请核对保存位置。')
        if not target.exists():
            self._publish_version(staged, target)
        self._require_directory(directory)
        return self._checked_material(target, staged.sha256)

    def _publish_version(self, staged: StagedWord, target: Path) -> None:
        directory = target.parent
        temporary = directory / ('.intake-' + uuid4().hex + '.tmp')
        # The private sibling can exceed legacy MAX_PATH even when the final Word
        # is readable by Explorer. Keep the user's final local/UNC path intact.
        if os.name == 'nt':
            native = str(temporary)
            if not native.startswith('\\\\?\\'):
                temporary = Path('\\\\?\\UNC\\' + native[2:] if native.startswith('\\\\') else '\\\\?\\' + native)
        move = os.rename if os.name == 'nt' else os.link
        # 已校验的暂存件移入版本目录，再从同目录原子落位。
        self._require_directory(directory)
        move(staged.path, temporary)
        try:
            self._require_directory(directory)
            self._checked_material(temporary, staged.sha256)
            try:
                move(temporary, target)
            except FileExistsError:
                pass  # 同时归档时由读回校验判断另一台是否落了同一份原件。
        finally:
            if directory.resolve() == directory:
                temporary.unlink(missing_ok=True)

    def _checked_material(self, target: Path, expected_sha: str) -> Material:
        if target.resolve() != target or not target.is_file():
            raise StorageError('暂时无法读取已保存的原件。')
        with target.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= _MAX_WORD_BYTES:
                raise StorageError('已保存的原件长度无效。')
            actual = file_digest(stream, 'sha256').hexdigest()
            if (actual != expected_sha or _identity(before) != _identity(os.fstat(stream.fileno()))
                    or _identity(before) != _identity(target.stat(follow_symlinks=False))):
                raise StorageError('已保存的原件无法通过完整性校验。')
        if target.resolve() != target:
            raise StorageError('原件落位发生路径重定向，请核对保存位置。')
        return Material(target.name, target, expected_sha)


@dataclass(frozen=True)
class Operation:
    actor_id: str
    task_id: str
    request_id: str
    name: str
    sha256: str
    initial_stage: str
    phase: str
    revision: str
    receipt_id: str | None


class Journal:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("""
                CREATE TABLE IF NOT EXISTS material_intake (
                    actor_id TEXT NOT NULL, task_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, sha256 TEXT NOT NULL, initial_stage TEXT NOT NULL,
                    phase TEXT NOT NULL CHECK (phase IN ('saved','begin','submit')),
                    revision TEXT NOT NULL, receipt_id TEXT,
                    PRIMARY KEY (actor_id, task_id)
                )
            """)
            yield db

    def _get(self, db, actor_id, task_id):
        row = db.execute('SELECT * FROM material_intake WHERE actor_id=? AND task_id=?', (actor_id, task_id)).fetchone()
        return Operation(**row) if row is not None else None

    def get(self, actor_id, task_id):
        with self._open() as db:
            return self._get(db, actor_id, task_id)

    def find_saved(self, actor_id, name):
        if not self.path.is_file():
            return ()
        with self._open() as db:
            return tuple(Operation(**row) for row in db.execute(
                'SELECT * FROM material_intake WHERE actor_id=? AND name=? AND phase=\'submit\' LIMIT 2',
                (actor_id, name)))

    def has_other_task(self, actor_id, task_id, staged):
        with self._open() as db:
            return self._has_other_task(db, actor_id, task_id, staged)

    def _has_other_task(self, db, actor_id, task_id, staged):
        return db.execute(
            'SELECT 1 FROM material_intake WHERE actor_id=? AND task_id<>? AND name=? AND sha256=? LIMIT 1',
            (actor_id, task_id, staged.name, staged.sha256)).fetchone() is not None

    def reserve(self, account, task, staged, automatic_selection):
        with self._open() as db:
            db.execute('BEGIN IMMEDIATE')
            if automatic_selection and self._has_other_task(db, account.id, task.id, staged):
                return None
            db.execute(
                'INSERT INTO material_intake VALUES (?, ?, ?, ?, ?, ?, \'saved\', ?, NULL) '
                'ON CONFLICT (actor_id, task_id) DO NOTHING',
                (account.id, task.id, uuid4().hex, staged.name, staged.sha256, task.stage.value, task.revision))
            return self._get(db, account.id, task.id)

    def advance(self, operation, phase, revision):
        with self._open() as db:
            db.execute('UPDATE material_intake SET phase=?,revision=? WHERE request_id=? AND phase=? AND revision=?',
                       (phase, revision, operation.request_id, operation.phase, operation.revision))
            return self._get(db, operation.actor_id, operation.task_id)

    def confirm(self, operation, receipt_id):
        with self._open() as db:
            db.execute('UPDATE material_intake SET receipt_id=? WHERE request_id=? AND phase=\'submit\' AND receipt_id IS NULL',
                       (receipt_id, operation.request_id))
            return self._get(db, operation.actor_id, operation.task_id).receipt_id == receipt_id

    @contextmanager
    def _open_preparations(self):
        with self._open() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS prepared_materials (
                    actor_id TEXT NOT NULL, task_id TEXT NOT NULL, revision TEXT NOT NULL,
                    stage TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL, relative_path TEXT,
                    PRIMARY KEY (actor_id, task_id, revision, stage, name, sha256)
                )
            """)
            yield db

    def _prepared_for_another_task(self, db, task_id, file):
        # 会话 ID 不是持久身份。这里仅查本机交件范围是否存在冲突，不读取旧原件。
        key = (task_id, file.name, file.sha256)
        return db.execute("""
            SELECT 1 FROM prepared_materials WHERE task_id<>? AND name=? AND sha256=?
            UNION ALL
            SELECT 1 FROM material_intake WHERE task_id<>? AND name=? AND sha256=?
            LIMIT 1
        """, key + key).fetchone() is not None

    def _preparation_key(self, account, task, file):
        return account.id, task.id, task.revision, task.stage.value, file.name, file.sha256

    def reserve_preparation(self, account, task, file, automatic_selection):
        with self._open_preparations() as db:
            db.execute('BEGIN IMMEDIATE')
            if automatic_selection and self._prepared_for_another_task(db, task.id, file):
                return False
            db.execute(
                'INSERT INTO prepared_materials VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT DO NOTHING',
                self._preparation_key(account, task, file))
        return True

    def record_preparation(self, account, task, material, relative_path):
        key = self._preparation_key(account, task, material)
        path = str(relative_path)
        match = 'actor_id=? AND task_id=? AND revision=? AND stage=? AND name=? AND sha256=?'
        with self._open_preparations() as db:
            db.execute(f'UPDATE prepared_materials SET relative_path=? WHERE {match} AND relative_path IS NULL',
                       (path, *key))
            row = db.execute(f'SELECT relative_path FROM prepared_materials WHERE {match}', key).fetchone()
            if row is None or row['relative_path'] != path:
                raise StorageError('交件准备记录发生变化，请核对后再试。')
