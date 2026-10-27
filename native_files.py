"""Windows handle-scoped ZIP operations; never execute or extract archive contents.

A movable lease denies other writers/deleters. Rename and final source removal
operate on that same handle, not on a path that could now name somebody else's file.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from concurrent.futures import CancelledError
from pathlib import Path


def cancelled(event):
    if event is not None and event.is_set():
        raise CancelledError('已取消；尚未完成移动的原件不会被删除。')


def plain_path(path: str | Path, *, allow_network=False) -> Path:
    raw = str(path).replace('/', '\\')
    if '\x00' in raw or raw.startswith(('\\\\?\\', '\\\\.\\', '\\??\\')):
        raise ValueError('不接受设备路径或文件流。')
    path = Path(os.path.abspath(Path(path).expanduser()))
    network = str(path).startswith('\\\\')
    if (network and not allow_network) or any(':' in part for part in path.parts[1:]):
        raise ValueError('仅接受普通文件；网络路径仅可作为保留原件的收录来源，不接受文件流。')
    if network and (not path.drive or ':' in path.drive):
        raise ValueError('请选择有效的共享文件路径。')
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('不移动符号链接、目录联接或云占位文件；请先保存为本地普通 ZIP。')
    return path


def _windows_path(path: str | Path) -> str:
    """将已校验的普通路径转换为WinAPI长路径，UNC使用专用前缀。"""
    value = str(path)
    if value.startswith('\\\\'):
        return '\\\\?\\UNC\\' + value[2:]
    return '\\\\?\\' + value


def path_is_within(path: str | Path, root: str | Path) -> bool:
    """按路径及现存目录标识核对边界，识别同一目录的UNC/映射盘别名。"""
    path = Path(os.path.abspath(Path(path).expanduser()))
    root = Path(os.path.abspath(Path(root).expanduser()))
    if path == root or path.is_relative_to(root):
        return True
    try:
        root_info = root.stat()
    except FileNotFoundError:
        # 不存在的保护目录没有可经其他别名读入的内容。
        return False
    if not root_info.st_ino:
        raise OSError('无法核对目录文件标识，未判断共享来源归属。')
    root_identity = (root_info.st_dev, root_info.st_ino)
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.stat()
        except FileNotFoundError:
            continue
        # 权限及网络错误必须向上传递，不能把核对失败当作边界之外。
        if not info.st_ino:
            raise OSError('无法核对来源文件标识，未判断共享来源归属。')
        if (info.st_dev, info.st_ino) == root_identity:
            return True
    return False


def identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _api():
    if os.name != 'nt':
        raise OSError('安全移动当前只支持 Windows。')
    from ctypes import wintypes as w
    dll = ctypes.WinDLL('kernel32', use_last_error=True)
    dll.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    dll.CreateFileW.restype = w.HANDLE
    dll.SetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    dll.SetFileInformationByHandle.restype = w.BOOL
    dll.CloseHandle.argtypes = [w.HANDLE]
    dll.CloseHandle.restype = w.BOOL
    return dll


def _volume_supports_named_streams(path: Path, api) -> bool | None:
    """查询文件实际所在卷；查询失败时不推断元数据不存在。"""
    from ctypes import wintypes as w
    api.GetVolumePathNameW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
    api.GetVolumePathNameW.restype = w.BOOL
    api.GetVolumeInformationW.argtypes = [
        w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD),
        ctypes.POINTER(w.DWORD), ctypes.POINTER(w.DWORD), w.LPWSTR, w.DWORD,
    ]
    api.GetVolumeInformationW.restype = w.BOOL
    # 目录挂载点可能属于其他卷，不能仅依据盘符根判断能力。
    volume = ctypes.create_unicode_buffer(max(261, len(str(path)) + 2))
    if not api.GetVolumePathNameW(str(path), volume, len(volume)):
        return None
    flags = w.DWORD()
    if not api.GetVolumeInformationW(volume.value, None, 0, None, None,
                                     ctypes.byref(flags), None, 0):
        return None
    return bool(flags.value & 0x00040000)  # FILE_NAMED_STREAMS


class ZipLease:
    def __init__(self, path, *, movable=True):
        self.path = plain_path(path, allow_network=not movable)
        self.movable = movable
        self.stream = None
        self.api = _api()

    def __enter__(self):
        import msvcrt
        before = self.path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('请选择普通 ZIP 文件，不能移动文件夹。')
        access = 0x80000000 | (0x10000 if self.movable else 0)  # READ, optionally DELETE
        handle = self.api.CreateFileW(_windows_path(self.path), access, 1, None, 3, 0x08200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.api.CloseHandle(handle)
            raise
        self.stream = os.fdopen(fd, 'rb')
        self.handle = handle
        self.initial = os.fstat(fd)
        if identity(before) != identity(self.initial):
            self.stream.close()
            raise RuntimeError('文件在打开时发生变化；未移动。')
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close()

    def inspect(self, *, cancel_event=None, progress=None):
        cancelled(cancel_event)
        info = os.fstat(self.stream.fileno())
        if info.st_size < 22:
            raise ValueError('ZIP 为空或文件不完整。')
        self.stream.seek(0)
        if self.stream.read(4) not in (b'PK\x03\x04', b'PK\x05\x06'):
            raise ValueError('没有可识别的 ZIP 文件头。')
        self.stream.seek(0)
        digest, count = hashlib.sha256(), 0
        for block in iter(lambda: self.stream.read(1024 * 1024), b''):
            cancelled(cancel_event)
            digest.update(block)
            count += len(block)
            if progress:
                progress(count, info.st_size)
        cancelled(cancel_event)
        if identity(info) != identity(os.fstat(self.stream.fileno())) or count != info.st_size:
            raise RuntimeError('读取期间文件发生变化；未移动。')
        return dict(path=str(self.path), filename=self.path.name, size=count,
                    sha256=digest.hexdigest(), mtime_ns=info.st_mtime_ns, identity=identity(info))

    def rename_to(self, target):
        if not self.movable:
            raise ValueError('只读校验句柄不能移动文件。')
        target = plain_path(target)
        if target.exists():
            raise FileExistsError('目标已存在；不覆盖旧文件。')
        from ctypes import wintypes as w
        class RenameInfo(ctypes.Structure):
            _fields_ = [('Flags', w.DWORD), ('RootDirectory', w.HANDLE),
                        ('FileNameLength', w.DWORD), ('FileName', w.WCHAR * 1)]
        encoded = str(target).encode('utf-16-le')
        buffer = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(encoded) + 2)
        value = ctypes.cast(buffer, ctypes.POINTER(RenameInfo)).contents
        value.Flags = 0  # ReplaceIfExists = FALSE
        value.RootDirectory = None
        value.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
        if not self.api.SetFileInformationByHandle(self.handle, 3, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.path = target

    def remove_after_verified_commit(self):
        if not self.movable:
            raise ValueError('此句柄没有移动授权。')
        disposition = ctypes.c_ubyte(1)
        if not self.api.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def zone_metadata(self):
        """Preserve Mark-of-the-Web on a cross-volume move; refuse unknown streams."""
        import msvcrt
        from ctypes import wintypes as w
        class StreamData(ctypes.Structure):
            _fields_ = [('StreamSize', ctypes.c_longlong), ('StreamName', w.WCHAR * 296)]
        self.api.FindFirstStreamW.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.FindFirstStreamW.restype = w.HANDLE
        self.api.FindNextStreamW.argtypes = [w.HANDLE, ctypes.c_void_p]
        self.api.FindNextStreamW.restype = w.BOOL
        self.api.FindClose.argtypes = [w.HANDLE]
        self.api.FindClose.restype = w.BOOL
        data = StreamData()
        finder = self.api.FindFirstStreamW(_windows_path(self.path), 0, ctypes.byref(data), 0)
        if finder == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error in (1, 38):  # no stream support / no stream found
                return None
            if error in (50, 87) and _volume_supports_named_streams(self.path, self.api) is False:
                return None
            raise ctypes.WinError(error)
        names = []
        try:
            while True:
                names.append(data.StreamName)
                if not self.api.FindNextStreamW(finder, ctypes.byref(data)):
                    error = ctypes.get_last_error()
                    if error != 38:
                        raise ctypes.WinError(error)
                    break
        finally:
            self.api.FindClose(finder)
        if any(name not in ('::$DATA', ':Zone.Identifier:$DATA') for name in names):
            raise ValueError('文件包含额外命名数据流；为避免丢失元数据，未执行跨盘移动或去重。')
        if ':Zone.Identifier:$DATA' not in names:
            return None
        handle = self.api.CreateFileW(_windows_path(self.path) + ':Zone.Identifier', 0x80000000, 5, None, 3, 0x08000000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            self.api.CloseHandle(handle)
            raise
        with os.fdopen(fd, 'rb') as stream:
            value = stream.read(65537)
        if len(value) > 65536:
            raise ValueError('安全来源标记异常过大；保留原文件。')
        return value
