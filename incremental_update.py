"""只读验证程序基线，优先下载受限增量包；失败时下载同目标完整安装包。"""
from __future__ import annotations

from concurrent.futures import CancelledError
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from native_files import plain_path
import update_client as client


def _validated_release(release):
    assets = [dict(name=release.filename, size=release.size, state='uploaded',
                   digest='sha256:' + release.sha256, browser_download_url=release.url)]
    delta = getattr(release, 'delta', None)
    if (isinstance(delta, client.DeltaAsset) and delta.from_tag == client.CURRENT_TAG
            and delta.to_tag == release.tag):
        assets.append(dict(name=delta.filename, size=delta.size, state='uploaded',
                           digest=('sha256:' + delta.sha256 if isinstance(delta.sha256, str) else None),
                           browser_download_url=delta.url))
    checked = client.parse_release(dict(tag_name=release.tag, draft=False, prerelease=False,
        html_url=client.RELEASE_ROOT + 'tag/' + release.tag, body=release.notes, assets=assets),
        current_tag=client.CURRENT_TAG)
    if checked is None:
        raise ValueError('不能下载同构建或旧构建作为升级。')
    return checked


def _check_pending(app_dir):
    from incremental_apply import has_pending_update
    if has_pending_update(app_dir):
        raise RuntimeError('程序更新恢复尚未完成，请先处理恢复记录；未下载或覆盖任何更新。')


def _installed_capable(app_dir):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return False
    try:
        root = plain_path(app_dir)
        executable = plain_path(sys.executable)
        helper = plain_path(root / 'WorkBoxUpdater.exe')
        return (root.is_dir() and executable == root / 'WorkBox.exe'
                and helper.is_file() and helper.stat().st_nlink == 1)
    except (OSError, ValueError):
        return False


def _read_delta(path, release, app_dir, *, cancel_event=None):
    from delta_format import read_manifest
    from incremental_apply import verify_tree
    client._cancel(cancel_event)
    path = plain_path(path)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('增量更新必须为本机单链接普通文件。')
    delta = release.delta
    manifest = read_manifest(path, expected_sha256=delta.sha256, expected_size=delta.size,
                             from_tag=delta.from_tag, to_tag=delta.to_tag)
    client._cancel(cancel_event)
    verify_tree(Path(app_dir), manifest['base'], cancel_event=cancel_event)
    client._cancel(cancel_event)
    return manifest


def verify_downloaded_update(path, release, app_dir):
    """停机前重复校验；完整 EXE 沿用原有校验接口，不执行应用。"""
    path = Path(path)
    if path.suffix.casefold() != '.wbdelta':
        return client.verify_installer(path, release)
    _check_pending(app_dir)
    checked = _validated_release(release)
    if checked.delta is None or path.name != checked.delta.filename or not _installed_capable(app_dir):
        raise ValueError('增量更新与当前安装或目标构建不匹配。')
    _read_delta(path, checked, app_dir)


def _cache_candidate(folder, delta):
    folder = plain_path(folder)
    if (re.fullmatch('[a-f0-9]{32}', folder.name) is None or not folder.is_dir()
            or {path.name for path in folder.iterdir()} != {delta.filename}):
        raise ValueError('增量更新缓存已被使用或含未知内容。')
    path = plain_path(folder / delta.filename)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('增量更新缓存不是单链接普通文件。')
    if os.name == 'nt':
        with Path(str(path) + ':Zone.Identifier').open(encoding='utf-8') as stream:
            if stream.read(4097) != '[ZoneTransfer]\nZoneId=3\nHostUrl=' + delta.url + '\n':
                raise ValueError('增量更新缓存的下载来源已变化。')
    return path


def _cached_delta(root, release, app_dir, *, cancel_event=None):
    for entry in root.iterdir():
        client._cancel(cancel_event)
        try:
            candidate = _cache_candidate(entry, release.delta)
            _read_delta(candidate, release, app_dir, cancel_event=cancel_event)
            # 包摘要读取期间也可能被助手认领，不复用已经写入结果/计划的缓存。
            if _cache_candidate(entry, release.delta) == candidate:
                return candidate
        except (OSError, ValueError):
            continue
    return None


def _previous_delta_failed(cache_root, release, *, cancel_event=None):
    root = plain_path(cache_root)
    if not root.exists():
        return False
    for entry in root.iterdir():
        client._cancel(cancel_event)
        if re.fullmatch('[a-f0-9]{32}', entry.name) is None:
            continue
        try:
            folder = plain_path(entry)
            if not folder.is_dir():
                continue
            receipt = plain_path(folder / 'install-result.json')
            if not receipt.is_file() or receipt.stat().st_nlink != 1 or receipt.stat().st_size > 16 * 1024:
                continue
            with receipt.open('rb') as stream:
                raw = stream.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                continue
            value = json.loads(raw.decode('utf-8'))
            if (isinstance(value, dict) and value.get('kind') == 'delta'
                    and value.get('release_tag') == release.tag
                    and value.get('delta_sha256') == release.delta.sha256
                    and value.get('status') in ('rolled-back', 'recovery-required', 'failed')):
                client._cancel(cancel_event)
                return True
        except (OSError, ValueError, RecursionError):
            # 回执仅用于选择完整包，不信任其中的路径、错误文本或命令。
            continue
    return False


def _download_delta(release, cache_root, app_dir, *, cancel_event=None, progress=None):
    client._cancel(cancel_event)
    delta = release.delta
    root = plain_path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    root = plain_path(root)
    cached = _cached_delta(root, release, app_dir, cancel_event=cancel_event)
    if cached is not None:
        if progress is not None:
            progress(delta.size, delta.size)
        client._cancel(cancel_event)
        return cached
    if shutil.disk_usage(root).free < delta.size + 64 * 1024 * 1024:
        raise OSError('更新缓存所在磁盘空间不足。')
    folder = root / uuid.uuid4().hex
    folder.mkdir()
    folder_identity = (folder.stat().st_dev, folder.stat().st_ino)
    partial = folder / (delta.filename + '.part')
    final = folder / delta.filename
    partial_identity = final_identity = None
    started, count = time.monotonic(), 0
    try:
        client._cancel(cancel_event)
        with client._open(delta.url) as response, partial.open('xb') as stream:
            info = os.fstat(stream.fileno())
            partial_identity = (info.st_dev, info.st_ino)
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdecimal() or int(declared) != delta.size):
                raise ValueError('服务器声明的增量包长度不一致。')
            while True:
                client._cancel(cancel_event)
                if time.monotonic() - started > 600:
                    raise TimeoutError('增量更新下载超时。')
                block = response.read(256 * 1024)
                if not block:
                    break
                count += len(block)
                if count > delta.size:
                    raise ValueError('增量包超过发布记录中的大小。')
                stream.write(block)
                if progress is not None:
                    progress(count, delta.size)
            stream.flush()
            os.fsync(stream.fileno())
        client._cancel(cancel_event)
        _read_delta(partial, release, app_dir, cancel_event=cancel_event)
        if {path.name for path in folder.iterdir()} != {partial.name}:
            raise ValueError('下载目录出现未知内容，未发布本次增量包。')
        plain_path(folder)
        plain_path(partial)
        if ((folder.stat().st_dev, folder.stat().st_ino) != folder_identity
                or (partial.stat().st_dev, partial.stat().st_ino) != partial_identity):
            raise ValueError('增量更新下载目录或文件标识已变化。')
        # 使用不覆盖的改名，任何已出现的目标都使本次尝试失败。
        if final.exists():
            raise ValueError('增量更新目标已存在。')
        partial.rename(final)
        final_identity, partial_identity = partial_identity, None
        if os.name == 'nt':
            Path(str(final) + ':Zone.Identifier').write_text(
                '[ZoneTransfer]\nZoneId=3\nHostUrl=' + delta.url + '\n', encoding='utf-8')
        client._cancel(cancel_event)
        return final
    except BaseException:
        # 只清理本次创建的文件；未知内容留在原处，不迁移或删除其他缓存。
        for path, owned in ((partial, partial_identity), (final, final_identity)):
            if owned is None:
                continue
            try:
                plain_path(folder)
                plain_path(path)
                if ((folder.stat().st_dev, folder.stat().st_ino) == folder_identity
                        and (path.stat().st_dev, path.stat().st_ino) == owned):
                    path.unlink()
            except (OSError, ValueError):
                pass
        try:
            plain_path(folder)
            if (folder.stat().st_dev, folder.stat().st_ino) == folder_identity:
                folder.rmdir()
        except (OSError, ValueError):
            pass
        raise


def download_update(release, cache_root, *, app_dir, cancel_event=None, progress=None, status=None):
    client._cancel(cancel_event)
    _check_pending(app_dir)
    checked = _validated_release(release)
    if checked.delta is not None and _installed_capable(app_dir):
        try:
            if _previous_delta_failed(cache_root, checked, cancel_event=cancel_event):
                raise ValueError('此目标版本的同一增量包此前更新失败，保留原回执')
            if status is not None:
                status('正在下载并校验增量更新…')
            path = _download_delta(checked, cache_root, app_dir,
                                   cancel_event=cancel_event, progress=progress)
            _check_pending(app_dir)
            return path
        except CancelledError:
            raise
        except (OSError, ValueError, HTTPException) as error:
            client._cancel(cancel_event)
            _check_pending(app_dir)
            if status is not None:
                status('增量更新不可用：' + str(error) + '。正在改用完整安装包…')
    elif status is not None:
        status('正在下载完整安装包…')
    client._cancel(cancel_event)
    _check_pending(app_dir)
    return client.download_release(checked, cache_root, cancel_event=cancel_event, progress=progress)
