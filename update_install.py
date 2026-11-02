"""Database recovery point and post-exit Windows installer handoff."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

from native_files import plain_path
from update_client import Release, verify_installer


def backup_database(database: Path, backup_root: Path) -> Path:
    """Caller must still own the data session lock and have stopped all workers."""
    database, root = plain_path(database), plain_path(backup_root)
    if not database.is_file():
        raise ValueError('数据库恢复点缺少源文件。')
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < database.stat().st_size * 2 + 64 * 1024 * 1024:
        raise OSError('空间不足，未创建数据库恢复点或执行升级。')
    folder = root / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12])
    folder.mkdir()
    target = folder / 'workbox.db'
    try:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)
                if destination.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise ValueError('数据库恢复点校验失败，未执行升级。')
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def handoff_command(installer: Path, release: Release, app_dir: Path, parent_pid: int,
                    data_dir: Path | None = None) -> list[str]:
    installer, app_dir = plain_path(installer), plain_path(app_dir)
    verify_installer(installer, release)
    if not (app_dir / 'WorkBox.exe').is_file() or type(parent_pid) is not int or parent_pid <= 0:
        raise ValueError('升级目标或等待的应用进程无效。')
    data_dir = plain_path(data_dir if data_dir is not None else app_dir / 'data')
    config = base64.b64encode(json.dumps(dict(installer=str(installer), app=str(app_dir),
        data=str(data_dir), pid=parent_pid, size=release.size, sha256=release.sha256,
        result=str(installer.parent/'install-result.json')), ensure_ascii=True).encode()).decode()
    # Only base64 JSON values enter this constant program; no paths become code.
    script = r'''
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
$lease = $null
try {
  $parent = $null
  try { $parent = [Diagnostics.Process]::GetProcessById([int]$cfg.pid) } catch [ArgumentException] {}
  if ($null -ne $parent -and -not $parent.WaitForExit(120000)) { throw 'WorkBox did not exit safely; no installation.' }
  $lease = [IO.File]::Open($cfg.installer, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  if ($lease.Length -ne $cfg.size) { throw 'Installer size changed.' }
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { $digest = [BitConverter]::ToString($hasher.ComputeHash($lease)).Replace('-', '').ToLowerInvariant() } finally { $hasher.Dispose() }
  if ($digest -ne $cfg.sha256) { throw 'Installer digest changed; installation refused.' }
  $arguments = '/SP- /NORESTART /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /WORKBOXUPDATE=1 /DIR="' + $cfg.app + '"'
  $job = Start-Process -FilePath $cfg.installer -ArgumentList $arguments -PassThru
  $job.WaitForExit()
  @{status='installer-exited';exit_code=$job.ExitCode} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
  if ($job.ExitCode -eq 0) {
    Start-Process -FilePath (Join-Path $cfg.app 'WorkBox.exe') -ArgumentList ('--panel --data-dir="' + $cfg.data + '"') -WorkingDirectory $cfg.app
  }
} catch {
  @{status='failed';error=$_.Exception.Message} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
  Add-Type -AssemblyName System.Windows.Forms
  [Windows.Forms.MessageBox]::Show('WorkBox update was not completed. Keep the downloaded installer and check install-result.json. Existing data was not reset.', 'WorkBox update') | Out-Null
} finally { if ($null -ne $lease) { $lease.Dispose() } }
'''.replace('__CONFIG__', config)
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return [str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
            '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()]


def launch_installer(installer: Path, release: Release, data_dir: Path):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        raise ValueError('仅支持Windows安装版内执行升级；源码运行不会替换开发目录。')
    command = handoff_command(installer, release, Path(sys.executable).resolve().parent,
                              os.getpid(), data_dir)
    return subprocess.Popen(command, close_fds=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
