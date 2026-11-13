"""更新器专用的 Windows 同句柄核验与无覆盖移动，不读取业务数据。"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat

from native_files import ZipLease, _api, _windows_path, plain_path


ANY = object()
_WINDOWS = os.name == 'nt'


def _supported():
    if not _WINDOWS:
        raise OSError('程序文件的同句柄安全移动仅支持 Windows。')


def _identity(info):
    return info.st_dev, info.st_ino


class _DirectoryLease:
    """固定目录对象，拒绝目录删除/改名；最终目标还绑定此句柄。"""
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        import msvcrt
        before = plain_path(self.path).stat()
        if not stat.S_ISDIR(before.st_mode) or not before.st_ino:
            raise ValueError('文件父目录不存在或无法核对身份。')
        api = _api()
        # 发布子文件需要共享写。该模式不阻止置 reparse，不能单独当作路径保护。
        handle = api.CreateFileW(_windows_path(self.path), 0x80000000, 3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
        try:
            opened = os.fstat(self.fd)
            after = plain_path(self.path).stat()
            if (not stat.S_ISDIR(opened.st_mode) or _identity(before) != _identity(opened)
                    or _identity(opened) != _identity(after)):
                raise ValueError('父目录在获取保护句柄时发生替换。')
            self.handle = handle
            self.identity = _identity(opened)
            return self
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def check(self):
        if _identity(plain_path(self.path).stat()) != self.identity:
            raise ValueError('文件父目录身份发生变化。')


@contextmanager
def _parents_locked(paths):
    parents = set()
    for path in paths:
        for parent in path.parents:
            parents.add(parent)
    with ExitStack() as stack:
        held = {}
        for parent in sorted(parents, key=lambda value: (len(value.parts), str(value).casefold())):
            held[parent] = stack.enter_context(_DirectoryLease(parent))
        # 获取全部父链保护后，重新拒绝重解析路径与缺失父目录。
        for path in paths:
            plain_path(path)
        yield held


def _native_rename(lease, parent, leaf):
    """Nt API 接受相对 RootDirectory；Win32 包装器要求它为空，不能用于此处。"""
    from ctypes import wintypes as w
    class RenameInfo(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', ctypes.c_ubyte), ('RootDirectory', w.HANDLE),
                    ('FileNameLength', w.ULONG), ('FileName', w.WCHAR * 1)]
    class IoStatus(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
    encoded = leaf.encode('utf-16-le')
    buffer = ctypes.create_string_buffer(ctypes.sizeof(RenameInfo) + len(encoded))
    value = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
    value.ReplaceIfExists = 0
    value.RootDirectory = parent.handle
    value.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
    native = ctypes.WinDLL('ntdll')
    native.NtSetInformationFile.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, w.ULONG, ctypes.c_int]
    native.NtSetInformationFile.restype = w.LONG
    native.RtlNtStatusToDosError.argtypes = [w.LONG]
    native.RtlNtStatusToDosError.restype = w.ULONG
    status = native.NtSetInformationFile(lease.handle, ctypes.byref(IoStatus()), buffer, len(buffer), 10)
    if status < 0:
        raise ctypes.WinError(native.RtlNtStatusToDosError(status))


class _MoveLease(ZipLease):
    """保持原文件句柄，但把无覆盖目标改名固定到持有的父目录对象。"""
    def __init__(self, path, parents):
        super().__init__(path, movable=True)
        self.parents = parents

    def _location(self, path):
        path = plain_path(path)
        current = path.stat()
        opened = os.fstat(self.stream.fileno())
        if _identity(current) != _identity(opened) or current.st_nlink != 1:
            raise ValueError('原程序文件路径不再指向持有的文件。')

    def rename_to(self, target):
        self._location(self.path)
        target = plain_path(target)
        parent = self.parents[target.parent]
        parent.check()
        _absent(target)
        _native_rename(self, parent, target.name)
        self.path = target
        # 即使改名途中有人为父目录置 reparse，也不能把隐藏位置误报成已发布。
        self._location(target)
        parent.check()


def _paths(*values):
    paths = tuple(plain_path(value) for value in values)
    if len({str(path).casefold() for path in paths}) != len(paths):
        raise ValueError('来源、目标与保留位置必须互不相同。')
    return paths


def _verified(lease, expected, expected_zone):
    if (not isinstance(expected, dict) or set(expected) != {'sha256', 'size'}
            or type(expected['size']) is not int or expected['size'] < 0
            or not isinstance(expected['sha256'], str)
            or re.fullmatch('[a-f0-9]{64}', expected['sha256']) is None):
        raise ValueError('缺少明确的程序文件长度和 SHA-256。')
    if expected_zone is not ANY and expected_zone is not None and not isinstance(expected_zone, bytes):
        raise ValueError('安全来源标记必须是字节或明确不存在。')
    before = os.fstat(lease.stream.fileno())
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != expected['size']:
        raise ValueError('程序文件不是预期的单链接普通文件。')
    digest = hashlib.sha256()
    lease.stream.seek(0)
    for block in iter(lambda: lease.stream.read(1024 * 1024), b''):
        digest.update(block)
    zone = lease.zone_metadata()  # 无论是否比较已知标记，都拒绝未知命名流。
    after = os.fstat(lease.stream.fileno())
    if (digest.hexdigest() != expected['sha256'] or _identity(before) != _identity(after)
            or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
            or after.st_nlink != 1 or (expected_zone is not ANY and zone != expected_zone)):
        raise ValueError('程序文件或安全来源标记与预期不一致。')


def _absent(path):
    if path.exists() or path.is_symlink():
        raise FileExistsError('目标位置已有内容，未覆盖：' + str(path))


def _same_volume(paths):
    if len({path.parent.stat().st_dev for path in paths}) != 1:
        raise ValueError('程序文件移动必须位于同一卷。')


def move_checked(source: Path, destination: Path, expected: dict, *, expected_zone=ANY) -> None:
    """核对持有的源文件并按同一句柄移入空位置，绝不覆盖。"""
    _supported()
    source, destination = _paths(source, destination)
    with _parents_locked((source, destination)) as parents:
        _same_volume((source, destination))
        _absent(destination)
        with _MoveLease(source, parents) as lease:
            _verified(lease, expected, expected_zone)
            lease.rename_to(destination)


def swap_checked(source: Path, destination: Path, source_expected: dict,
                 destination_expected: dict | None, retired: Path, *,
                 source_zone=ANY, destination_zone=ANY) -> None:
    """旧目标按原句柄保留，再发布来源；失败时仅向空目标恢复旧句柄。"""
    _supported()
    source, destination, retired = _paths(source, destination, retired)
    with _parents_locked((source, destination, retired)) as parents, ExitStack() as stack:
        _same_volume((source, destination, retired))
        _absent(retired)
        incoming = stack.enter_context(_MoveLease(source, parents))
        _verified(incoming, source_expected, source_zone)
        if destination_expected is None:
            _absent(destination)
            incoming.rename_to(destination)
            return
        previous = stack.enter_context(_MoveLease(destination, parents))
        _verified(previous, destination_expected, destination_zone)
        previous.rename_to(retired)
        try:
            incoming.rename_to(destination)
        except BaseException:
            # 最终内核改名也禁止覆盖；即便外部程序在空位检查后抢占，仍保留原件。
            if not destination.exists() and not destination.is_symlink():
                try:
                    previous.rename_to(destination)
                except (OSError, ValueError, RuntimeError):
                    pass
            raise
