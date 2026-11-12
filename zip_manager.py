"""Read-only ZIP metadata and conservative filename matching. No extraction."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import unicodedata
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bundle_analyzer import bundle_hint
from file_matcher import match_annotated_project_names

Progress = Callable[[int, int], None]


@dataclass(frozen=True)
class ZipInfo:
    path: str
    filename: str
    size: int
    sha256: str
    mtime_ns: int


def inspect_zip(
    path: str | Path, *, cancel_event: threading.Event | None = None,
    progress: Progress | None = None,
) -> ZipInfo:
    def cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("已取消 ZIP 读取。")

    cancelled()
    source = Path(path).expanduser().resolve(strict=True)
    if source.suffix.casefold() != ".zip":
        raise ValueError("仅接受 .zip 文件。")
    before = source.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size < 22:
        raise ValueError("请选择有效的 ZIP 文件，而不是文件夹或空文件。")
    digest = hashlib.sha256()
    read_size = 0
    with source.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if stream.read(4) not in (b"PK\x03\x04", b"PK\x05\x06"):
            raise ValueError("文件没有可识别的 ZIP 文件头。")
        stream.seek(0)
        while True:
            cancelled()
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read_size += len(chunk)
            if progress is not None:
                progress(read_size, opened.st_size)
        final_handle = os.fstat(stream.fileno())
    cancelled()
    try:
        after = source.stat()
    except FileNotFoundError as exc:
        raise RuntimeError("ZIP 在读取期间已被外部程序移走，请重新选择。") from exc

    def identity(value: os.stat_result) -> tuple:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    if not (identity(before) == identity(opened) == identity(final_handle) == identity(after)) or read_size != before.st_size:
        raise RuntimeError("ZIP 在读取期间发生变化，未登记本次结果，请重新拖入。")
    return ZipInfo(str(source), source.name, read_size, digest.hexdigest(), before.st_mtime_ns)


CODE = re.compile(r"(?<![a-z0-9])WB[-_](\d{1,12})(?!\d)", re.IGNORECASE)
DOWNLOAD_COPY = re.compile(r"(?P<name>.+) \([1-9]\d*\)$")
SUFFIX = re.compile(
    r"[\s_.\-（(【\[]+(?:v?\d+\.\d+(?:\.\d+)*|20\d{6}|20\d{2}[-.]\d{2}[-.]\d{2}|"
    r"final|release|delivery|交付包|交付|成品|完成版|正式版|最终版|"
    r"[^ _./\\-]{1,20}版)[\s）)】\]]*$",
    re.IGNORECASE,
)


def _key(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).casefold() if ch.isalnum())


def match_projects(filename: str, projects: list[dict], *, include_annotations=True) -> list[dict]:
    """Return all equally strongest candidates, not a guessed winner."""
    stem = Path(filename).stem
    codes = {int(value) for value in CODE.findall(stem)}
    if codes:
        return [project for project in projects if project["id"] in codes or
                any(type(value) is int and value in codes for value in project.get('merged_project_ids', []))]
    variants = set()
    while True:
        variants.add(_key(stem))
        direct = [project for project in projects if _key(project["name"]) == _key(stem)]
        if direct:
            return direct
        copy = DOWNLOAD_COPY.fullmatch(unicodedata.normalize("NFKC", stem))
        if copy is None:
            break
        stem = copy.group('name')
    while True:
        shorter = SUFFIX.sub("", stem)
        if shorter == stem:
            break
        variants.add(_key(shorter))
        stem = shorter
    with_batch = [
        project for project in projects
        if project.get("batch_name") and (
            _key(project["batch_name"] + project["name"]) in variants
            or _key(project["name"] + project["batch_name"]) in variants
        )
    ]
    if with_batch:
        return with_batch
    current = [project for project in projects if _key(project["name"]) in variants]
    if current:
        return current
    # 沿用 ZIP 的完整正规化规则，旧名仅在所有当前名称规则无结果时兜底。
    aliases_with_batch = [project for project in projects if project.get('batch_name') and any(
        isinstance(alias, str) and (_key(project['batch_name'] + alias) in variants
            or _key(alias + project['batch_name']) in variants)
        for alias in project.get('name_aliases', []))]
    if aliases_with_batch:
        return aliases_with_batch
    aliases = [project for project in projects if any(isinstance(alias, str) and _key(alias) in variants
               for alias in project.get('name_aliases', []))]
    if aliases:
        return aliases
    # 末尾件数仍交给合包检查，不能把多项目 ZIP 当成一个项目的普通附注。
    if include_annotations and not bundle_hint(filename):
        return match_annotated_project_names(Path(filename).stem, projects)['candidates']
    return []
