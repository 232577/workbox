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
        tag=release.tag, probe=str(installer.parent/('installed-build-'+uuid.uuid4().hex+'.json')),
        result=str(installer.parent/'install-result.json')), ensure_ascii=True).encode()).decode()
    # Only base64 JSON values enter this constant program; no paths become code.
    script = r'''
$ErrorActionPreference = 'Stop'
$cfg = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
$lease = $null
$installerExitCode = $null
try {
  @{status='helper-started';release_tag=$cfg.tag} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
  $parent = $null
  try { $parent = [Diagnostics.Process]::GetProcessById([int]$cfg.pid) } catch [ArgumentException] {}
  if ($null -ne $parent -and -not $parent.WaitForExit(120000)) { throw 'WorkBox did not exit safely; no installation.' }
  $lease = [IO.File]::Open($cfg.installer, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  if ($lease.Length -ne $cfg.size) { throw 'Installer size changed.' }
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { $digest = [BitConverter]::ToString($hasher.ComputeHash($lease)).Replace('-', '').ToLowerInvariant() } finally { $hasher.Dispose() }
  if ($digest -ne $cfg.sha256) { throw 'Installer digest changed; installation refused.' }
  $arguments = '/SP- /SILENT /NORESTART /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /WORKBOXUPDATE=1 /DIR="' + $cfg.app + '"'
  $job = Start-Process -FilePath $cfg.installer -ArgumentList $arguments -PassThru
  $job.WaitForExit()
  $installerExitCode = $job.ExitCode
  @{status='installer-exited';exit_code=$installerExitCode} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
  if ($installerExitCode -ne 0) { throw ('Installer exited with code ' + $installerExitCode + '.') }
  $program = Join-Path $cfg.app 'WorkBox.exe'
  $probeArgs = '--build-info --build-info-output="' + $cfg.probe + '" --data-dir="' + $cfg.data + '"'
  $probe = Start-Process -FilePath $program -ArgumentList $probeArgs -WorkingDirectory $cfg.app -PassThru
  if (-not $probe.WaitForExit(30000) -or $probe.ExitCode -ne 0) { throw 'Installed build probe failed; application was not restarted.' }
  if (-not [IO.File]::Exists($cfg.probe) -or (Get-Item -LiteralPath $cfg.probe).Length -gt 4096) { throw 'Installed build identity is missing or invalid.' }
  $actual = [IO.File]::ReadAllText($cfg.probe, [Text.Encoding]::UTF8) | ConvertFrom-Json
  if ($actual.release_tag -cne $cfg.tag -or $actual.version -cne '1.0.0') { throw 'Installed release tag does not match the requested update.' }
  if (-not [string]::Equals([IO.Path]::GetFullPath($actual.data_dir), [IO.Path]::GetFullPath($cfg.data), [StringComparison]::OrdinalIgnoreCase)) { throw 'Installed data path does not match the original session.' }
  $started = Start-Process -FilePath $program -ArgumentList ('--panel --data-dir="' + $cfg.data + '"') -WorkingDirectory $cfg.app -PassThru
  @{status='verified-relaunch-requested';release_tag=$actual.release_tag;data_dir=$actual.data_dir;pid=$started.Id;exit_code=0} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
} catch {
  try {
    @{status='failed';error=$_.Exception.Message;exit_code=$installerExitCode} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $cfg.result
  } finally {
    Add-Type -AssemblyName System.Windows.Forms
    [Windows.Forms.MessageBox]::Show('更新未完成。请保留安装包和数据库恢复点。' + [Environment]::NewLine + '安装记录：' + $cfg.result, 'WorkBox 更新') | Out-Null
  }
} finally { if ($null -ne $lease) { $lease.Dispose() } }
'''.replace('__CONFIG__', config)
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return [str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
            '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()]


def delta_handoff_command(package: Path, release: Release, app_dir: Path, parent_pid: int,
                          data_dir: Path) -> list[str]:
    """复制已核实的独立助手，运行副本避免锁住被更新的安装载荷。"""
    from incremental_update import _validated_release
    from delta_format import read_manifest
    from incremental_apply import verify_tree, has_pending_update, _copy_checked
    package, app_dir, data_dir = map(plain_path, (package, app_dir, data_dir))
    checked = _validated_release(release)
    delta = checked.delta
    if (delta is None or package.name != delta.filename or type(parent_pid) is not int
            or not 0 < parent_pid <= 0xFFFFFFFF):
        raise ValueError('增量更新交接信息无效。')
    if has_pending_update(app_dir):
        raise RuntimeError('上次程序更新尚未恢复，未覆盖现场。')
    manifest = read_manifest(package, expected_sha256=delta.sha256, expected_size=delta.size,
                             from_tag=delta.from_tag, to_tag=delta.to_tag)
    verify_tree(app_dir, manifest['base'])
    cache = plain_path(package.parent)
    if {path.name for path in cache.iterdir()} != {package.name}:
        raise ValueError('本次更新缓存含既有交接或未知文件，请重新下载。')
    helper = plain_path(app_dir / 'WorkBoxUpdater.exe')
    with helper.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('独立更新助手不是有效的 Windows 程序。')
    value = dict(format='workbox-incremental-plan-v1', app_dir=str(app_dir), data_dir=str(data_dir),
                 parent_pid=parent_pid, package=str(package), expected_sha256=delta.sha256,
                 expected_size=delta.size, from_tag=delta.from_tag, to_tag=delta.to_tag)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')
    if len(encoded) > 16384:
        raise ValueError('更新交接计划超过允许大小。')
    copied = cache / ('updater-' + uuid.uuid4().hex + '.exe')
    _copy_checked(helper, copied, manifest['base']['WorkBoxUpdater.exe'])
    plan = plain_path(cache / 'incremental-plan.json')
    with plan.open('xb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return [str(copied), '--plan', str(plan)]


def launch_installer(installer: Path, release: Release, data_dir: Path):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        raise ValueError('仅支持Windows安装版内执行升级；源码运行不会替换开发目录。')
    prepare = delta_handoff_command if Path(installer).suffix.casefold() == '.wbdelta' else handoff_command
    command = prepare(installer, release, Path(sys.executable).resolve().parent, os.getpid(), data_dir)
    # Windows PowerShell can exit 0 without running an EncodedCommand when it
    # inherits DETACHED_PROCESS from a windowless parent. Keep a hidden console
    # subsystem instead; the helper must outlive this process and run its script.
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
    return subprocess.Popen(command, close_fds=True, env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
