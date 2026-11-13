"""安装版增量助手复制与启动保护，不启动真实安装或业务程序。"""
import hashlib
import json
from pathlib import Path
import sys

import pytest

import main
import update_install
from update_client import Release, DeltaAsset, RELEASE_ROOT
from test_incremental_apply import candidate, OLD, NEW, preserved


def selected(candidate):
    app, old_package, manifest, args = candidate
    cache = old_package.parent / 'cache'
    cache.mkdir()
    package = old_package.rename(cache / old_package.name)
    filename = 'WorkBox-1.0.0-Setup-x64-20261113-r3.exe'
    delta = DeltaAsset(OLD, NEW, package.name, args['expected_size'], args['expected_sha256'],
                       RELEASE_ROOT + 'download/' + NEW + '/' + package.name)
    release = Release(NEW, filename, args['expected_size'] + 1000, '0' * 64,
                      RELEASE_ROOT + 'download/' + NEW + '/' + filename, 'anonymous', delta)
    return app, package, manifest, args, release


def test_delta_handoff_copies_verified_helper_and_writes_bounded_plan(candidate):
    app, package, manifest, args, release = selected(candidate)
    command = update_install.delta_handoff_command(package, release, app, 2147483647, args['data_dir'])
    copied = Path(command[0])
    assert copied.parent == package.parent and copied != app / 'WorkBoxUpdater.exe'
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == manifest['base']['WorkBoxUpdater.exe']['sha256']
    assert command[1] == '--plan' and '--noninteractive' not in command
    plan_path = Path(command[2])
    assert plan_path.name == 'incremental-plan.json' and plan_path.stat().st_size <= 16384
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    assert plan['format'] == 'workbox-incremental-plan-v1'
    assert plan['app_dir'] == str(app) and plan['data_dir'] == str(args['data_dir'])
    assert plan['parent_pid'] == 2147483647 and plan['package'] == str(package)
    assert plan['expected_sha256'] == args['expected_sha256']
    with pytest.raises((ValueError, FileExistsError)):
        update_install.delta_handoff_command(package, release, app, 2147483647, args['data_dir'])
    preserved(app)


def test_changed_helper_or_unknown_cache_refuses_handoff(candidate):
    app, package, manifest, args, release = selected(candidate)
    (app / 'WorkBoxUpdater.exe').write_bytes(b'unknown helper')
    with pytest.raises(ValueError):
        update_install.delta_handoff_command(package, release, app, 2147483647, args['data_dir'])
    assert not (package.parent / 'incremental-plan.json').exists()
    preserved(app)


def test_frozen_delta_launch_uses_hidden_independent_helper(candidate, monkeypatch):
    app, package, manifest, args, release = selected(candidate)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(app / 'WorkBox.exe'))
    calls = []
    monkeypatch.setattr(update_install.subprocess, 'Popen', lambda command, **kwargs: calls.append((command, kwargs)))
    update_install.launch_installer(package, release, args['data_dir'])
    command, options = calls[0]
    assert command[1] == '--plan'
    assert options['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert options['creationflags'] & update_install.subprocess.CREATE_NO_WINDOW
    assert options.get('shell', False) is False
    preserved(app)


def test_frozen_startup_rejects_unresolved_transaction_before_data(tmp_path, monkeypatch):
    app = tmp_path / 'app'
    app.mkdir()
    (app / ('.workbox-update-' + 'b' * 32)).mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(app / 'WorkBox.exe'))
    errors = []
    monkeypatch.setattr(main, '_startup_error', errors.append)
    monkeypatch.setattr(main, '_main', lambda *a: pytest.fail('business opened before recovery'))
    assert main.main(['--panel']) == 1
    assert errors and '恢复' in errors[0]


def test_application_holds_runtime_lease_until_exit_and_probe_is_read_only(tmp_path, monkeypatch):
    from incremental_apply import runtime_guard
    app = tmp_path / 'app'
    app.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(app / 'WorkBox.exe'))
    def running(argv):
        with pytest.raises((OSError, RuntimeError)):
            with runtime_guard(app, exclusive=True):
                pass
        return 0
    monkeypatch.setattr(main, '_main', running)
    assert main.main(['--panel']) == 0
    with runtime_guard(app, exclusive=True):
        monkeypatch.setattr(main, '_main', lambda argv: 7)
        assert main.main(['--build-info']) == 7
