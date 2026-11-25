"""本机保存员工明确选择的真实交件目录。"""

import hashlib
import json
import os
from pathlib import Path
import tempfile


def shared_root(path: Path) -> Path:
    try:
        directory = path.expanduser().resolve(strict=True)
        if directory.is_dir():
            return directory
    except (OSError, RuntimeError, ValueError):
        pass
    raise ValueError('请选择已存在、可以访问的交件目录。')


def load_shared_root(settings: Path) -> Path | None:
    try:
        data = json.loads(settings.read_text(encoding='utf-8'))
        location = Path(data['shared_root'])
        return location if location.is_absolute() else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_shared_root(settings: Path, path: Path) -> None:
    directory = shared_root(path)
    settings.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=settings.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            json.dump({'shared_root': str(directory)}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(settings)
    finally:
        temp.unlink(missing_ok=True)


def staging_journal(data_root: Path, directory: Path) -> Path:
    identity = hashlib.sha256(os.path.normcase(str(directory)).encode('utf-8')).hexdigest()[:24]
    return data_root / '交件记录' / f'{identity}.sqlite'
