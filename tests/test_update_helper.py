"""标准库助手的隔离入口验收；成功替换/重启使用明确的边界替身。"""
from contextlib import nullcontext
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from delta_format import delta_filename
from delta_format import FORMAT, REQUIRED_FILES
from incremental_apply import UpdateApplyError


OLD, NEW = 'v1.0.0.20261113.2', 'v1.0.0.20261113.3'
ROOT = Path(__file__).resolve().parents[1]


def helper():
    return importlib.import_module('update_helper_main')


@pytest.fixture
def plan(tmp_path):
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'WorkBox.exe').write_bytes(b'MZanonymous fixture, never executed')
    data = app / 'data'
    data.mkdir()
    (data / 'workbox.db').write_bytes(b'private sentinel; not a database')
    cache = tmp_path / 'cache'
    cache.mkdir()
    package = cache / delta_filename(OLD, NEW)
    package.write_bytes(b'an invalid anonymous package; no program is executed')
    value = dict(format='workbox-incremental-plan-v1', app_dir=str(app), data_dir=str(data),
        parent_pid=2147483646, package=str(package), expected_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
        expected_size=package.stat().st_size, from_tag=OLD, to_tag=NEW)
    path = cache / 'incremental-plan.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


def write_plan(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def receipt(path):
    return json.loads((path.parent / 'install-result.json').read_text(encoding='utf-8'))


def test_load_plan_returns_exact_validated_paths_without_receipt(plan):
    path, value = plan
    loaded = helper().load_plan(path)
    assert set(loaded) == set(value)
    for key in ('app_dir', 'data_dir', 'package'):
        assert loaded[key] == Path(value[key])
    assert not (path.parent / 'install-result.json').exists()


@pytest.mark.parametrize('key,value', [('extra', True), ('format', 'unknown'), ('parent_pid', True),
    ('parent_pid', 0), ('parent_pid', 2**32), ('expected_size', True), ('expected_size', 0),
    ('expected_size', 256 * 1024 * 1024 + 1), ('expected_sha256', 'A' * 64),
    ('expected_sha256', 'a' * 63), ('from_tag', NEW), ('to_tag', OLD),
    ('app_dir', '.'), ('data_dir', 'relative'), ('package', 'relative.wbdelta')])
def test_invalid_plan_fields_fail_before_receipt_or_apply(plan, key, value):
    path, config = plan
    config[key] = value
    write_plan(path, config)
    with pytest.raises((ValueError, OSError)):
        helper().load_plan(path)
    assert not (path.parent / 'install-result.json').exists()


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'oversize', 'wrong-name', 'outside-package', 'hardlink'])
def test_plan_envelope_refuses_ambiguous_or_unowned_files(plan, tmp_path, kind):
    path, config = plan
    if kind == 'missing':
        del config['data_dir']
        write_plan(path, config)
    elif kind == 'duplicate':
        path.write_text(json.dumps(config)[:-1] + ',"parent_pid":123}', encoding='utf-8')
    elif kind == 'oversize':
        path.write_bytes(b' ' * (16 * 1024 + 1))
    elif kind == 'wrong-name':
        target = path.with_name('other-plan.json')
        path.rename(target)
        path = target
    elif kind == 'outside-package':
        target = tmp_path / Path(config['package']).name
        target.write_bytes(Path(config['package']).read_bytes())
        config['package'] = str(target)
        write_plan(path, config)
    else:
        os.link(path, path.with_name('plan-link.json'))
    with pytest.raises((ValueError, OSError)):
        helper().load_plan(path)


def test_successful_apply_boundary_is_not_a_frozen_end_to_end_test(plan, monkeypatch):
    h = helper()
    path, config = plan
    calls = []
    monkeypatch.setattr(h, 'wait_for_parent', lambda pid: calls.append(('wait', pid)))

    def apply(package, app_dir, **kwargs):
        assert receipt(path)['status'] == 'helper-started'
        assert calls == [('wait', config['parent_pid'])]
        assert package == Path(config['package']) and app_dir == Path(config['app_dir'])
        assert kwargs == {key: (Path(value) if key == 'data_dir' else value) for key, value in config.items()
                          if key in {'expected_sha256', 'expected_size', 'from_tag', 'to_tag', 'data_dir'}}
        calls.append(('apply',))
        return {'status': 'committed', 'transaction_dir': str(app_dir / ('.workbox-update-' + 'a' * 32))}

    monkeypatch.setattr(h, 'apply_update', apply)
    launched = []
    monkeypatch.setattr(h.subprocess, 'Popen', lambda args, **kwargs: launched.append((args, kwargs)) or type('Child', (), {'pid': 12345})())
    assert h.main(['--plan', str(path), '--noninteractive']) == 0
    result = receipt(path)
    assert result['kind'] == 'delta' and result['release_tag'] == NEW
    assert result['delta_sha256'] == config['expected_sha256']
    assert result['status'] == 'verified-relaunch-requested' and result['pid'] == 12345
    args, kwargs = launched[0]
    assert args == [str(Path(config['app_dir']) / 'WorkBox.exe'), '--panel', '--data-dir=' + config['data_dir']]
    assert kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert kwargs['shell'] is False and kwargs['cwd'] == Path(config['app_dir'])
    assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
    assert Path(config['data_dir'], 'workbox.db').read_bytes() == b'private sentinel; not a database'


@pytest.mark.parametrize('rollback,status,relaunch', [(True, 'rolled-back', True), (False, 'recovery-required', False)])
def test_apply_failure_restarts_only_after_confirmed_file_rollback(plan, monkeypatch, rollback, status, relaunch):
    h = helper()
    path, config = plan
    monkeypatch.setattr(h, 'wait_for_parent', lambda pid: None)

    def fail(*args, **kwargs):
        raise UpdateApplyError('匿名替换失败', transaction_dir=Path(config['app_dir']) / ('.workbox-update-' + 'a' * 32),
                               rollback_complete=rollback)

    monkeypatch.setattr(h, 'apply_update', fail)
    calls = []
    monkeypatch.setattr(h, 'relaunch', lambda *args: calls.append(args) or 55)
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert receipt(path)['status'] == status
    assert bool(calls) == relaunch


def test_restart_failure_after_commit_never_rolls_back_program_or_database(plan, monkeypatch):
    h = helper()
    path, _ = plan
    monkeypatch.setattr(h, 'wait_for_parent', lambda pid: None)
    monkeypatch.setattr(h, 'apply_update', lambda *args, **kwargs: {'status': 'committed'})
    monkeypatch.setattr(h, 'recover_update', lambda *args: pytest.fail('提交后不得回滚数据库或程序'))
    monkeypatch.setattr(h, 'relaunch', lambda *args: (_ for _ in ()).throw(OSError('匿名启动失败')))
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert receipt(path)['status'] == 'installed-restart-needed'


@pytest.mark.parametrize('failure', [PermissionError('anonymous denied'), TimeoutError('anonymous timeout')])
def test_wait_refusal_does_not_apply_or_restart(plan, monkeypatch, failure):
    h = helper()
    path, _ = plan
    monkeypatch.setattr(h, 'wait_for_parent', lambda pid: (_ for _ in ()).throw(failure))
    monkeypatch.setattr(h, 'apply_update', lambda *args, **kwargs: pytest.fail('父进程未退出不得替换'))
    monkeypatch.setattr(h, 'relaunch', lambda *args: pytest.fail('父进程未退出不得启动'))
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert receipt(path)['status'] == 'failed'


def test_existing_unknown_receipt_is_not_overwritten(plan, monkeypatch):
    h = helper()
    path, _ = plan
    result = path.parent / 'install-result.json'
    result.write_bytes(b'unknown owner content')
    monkeypatch.setattr(h, 'apply_update', lambda *args, **kwargs: pytest.fail('既有收据必须拒绝'))
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert result.read_bytes() == b'unknown owner content'


def test_receipt_creation_rejects_same_content_path_replacement(plan, monkeypatch):
    h = helper()
    path, _ = plan
    loaded = h.load_plan(path)
    original = h._regular
    replaced = []

    def replace_before_stat(target, maximum=None):
        if target.name == 'install-result.json' and not replaced:
            replaced.append(True)
            target.rename(target.with_name('retained-original.json'))
            target.write_bytes(target.with_name('retained-original.json').read_bytes())
        return original(target, maximum)

    monkeypatch.setattr(h, '_regular', replace_before_stat)
    with pytest.raises(ValueError):
        h._Receipt(path, loaded)


def test_atomic_receipt_write_keeps_identity_of_its_created_file(plan, monkeypatch):
    h = helper()
    path, _ = plan
    result = h._Receipt(path, h.load_plan(path))
    original = h.os.replace

    def replace_then_swap(source, destination):
        original(source, destination)
        destination.rename(destination.with_name('retained-final.json'))
        destination.write_bytes(destination.with_name('retained-final.json').read_bytes())

    monkeypatch.setattr(h.os, 'replace', replace_then_swap)
    with pytest.raises(ValueError):
        result.write('failed', error='anonymous failure')


def test_receipt_replaced_during_apply_is_preserved(plan, monkeypatch):
    h = helper()
    path, _ = plan
    monkeypatch.setattr(h, 'wait_for_parent', lambda pid: None)

    def apply(*args, **kwargs):
        (path.parent / 'install-result.json').write_bytes(b'unknown replacement')
        return {'status': 'committed'}

    monkeypatch.setattr(h, 'apply_update', apply)
    monkeypatch.setattr(h, 'relaunch', lambda *args: pytest.fail('收据遭替换后不得自动启动'))
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert (path.parent / 'install-result.json').read_bytes() == b'unknown replacement'


def test_explicit_recovery_reports_actual_result_without_business_restart(plan, monkeypatch, capsys):
    h = helper()
    _, config = plan
    transaction = Path(config['app_dir']) / ('.workbox-update-' + 'a' * 32)
    transaction.mkdir()
    calls = []
    monkeypatch.setattr(h, 'recover_update', lambda *args: calls.append(args) or {'status': 'rolled-back'})
    monkeypatch.setattr(h, 'relaunch', lambda *args: pytest.fail('显式恢复不自动启动业务'))
    assert h.main(['--recover', str(transaction), '--app-dir', config['app_dir'], '--noninteractive']) == 0
    assert calls == [(transaction, Path(config['app_dir']))]
    assert json.loads(capsys.readouterr().out)['status'] == 'rolled-back'


@pytest.mark.parametrize('kind', ['none', 'multiple', 'corrupt', 'declined', 'accepted'])
def test_frozen_no_args_requires_one_valid_pending_transaction_and_explicit_yes(plan, monkeypatch, kind):
    h = helper()
    _, config = plan
    app = Path(config['app_dir'])
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(app / 'WorkBoxUpdater.exe'))
    monkeypatch.setattr(h, '_notify', lambda *args, **kwargs: None)
    monkeypatch.setattr(h, 'runtime_guard', lambda *args, **kwargs: nullcontext())
    if kind != 'none':
        (app / ('.workbox-update-' + 'a' * 32)).mkdir()
    if kind == 'multiple':
        (app / ('.workbox-update-' + 'b' * 32)).mkdir()

    def journal(*args):
        if kind == 'corrupt':
            raise ValueError('匿名损坏记录')
        return {'phase': 'applying'}

    monkeypatch.setattr(h, '_journal', journal)
    prompts = []
    monkeypatch.setattr(h, '_ask_recovery', lambda *args: prompts.append(args) or kind == 'accepted')
    recovered = []
    monkeypatch.setattr(h, 'recover_update', lambda *args: recovered.append(args) or {'status': 'rolled-back'})
    monkeypatch.setattr(h, 'relaunch', lambda *args: pytest.fail('恢复不自动启动业务'))
    code = h.main([])
    assert bool(recovered) == (kind == 'accepted')
    assert bool(prompts) == (kind in {'accepted', 'declined'})
    assert (code == 0) == (kind in {'accepted', 'declined'})


def test_source_no_args_and_windowless_self_check_never_scan_app(monkeypatch):
    h = helper()
    monkeypatch.delattr(sys, 'frozen', raising=False)
    monkeypatch.setattr(h, '_scan_pending', lambda *args: pytest.fail('源码无参数不得扫描程序目录'))
    assert h.main([]) == 0
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    assert h.main(['--self-check']) == 0


@pytest.mark.skipif(os.name != 'nt', reason='Win32 process-handle waiting')
def test_real_parent_wait_handles_exited_alive_and_timeout_without_killing():
    h = helper()
    flags = subprocess.CREATE_NO_WINDOW
    exited = subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=flags)
    exited.wait(timeout=5)
    h.wait_for_parent(exited.pid, timeout_ms=1000)
    alive = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.5)'], creationflags=flags)
    try:
        with pytest.raises(TimeoutError):
            h.wait_for_parent(alive.pid, timeout_ms=20)
        assert alive.poll() is None
        h.wait_for_parent(alive.pid, timeout_ms=2000)
        assert alive.wait(timeout=2) == 0
    finally:
        if alive.poll() is None:
            alive.terminate()
            alive.wait(timeout=5)


@pytest.mark.skipif(os.name != 'nt', reason='isolated Windows helper CLI')
@pytest.mark.parametrize('parent_alive', [False, True])
def test_real_cli_waits_then_rejects_invalid_package_without_running_fixture(plan, parent_alive):
    path, config = plan
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.6)' if parent_alive else 'pass'],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    if not parent_alive:
        child.wait(timeout=5)
    config['parent_pid'] = child.pid
    write_plan(path, config)
    try:
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(ROOT / 'update_helper_main.py'), '--plan', str(path), '--noninteractive'],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode != 0
        assert child.poll() is not None
        if parent_alive:
            assert time.monotonic() - started >= .35
        assert receipt(path)['status'] == 'failed'
        assert Path(config['app_dir'], 'WorkBox.exe').read_bytes() == b'MZanonymous fixture, never executed'
        assert Path(config['data_dir'], 'workbox.db').read_bytes() == b'private sentinel; not a database'
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_real_cli_rejects_plan_before_creating_receipt(plan):
    path, config = plan
    config['parent_pid'] = True
    write_plan(path, config)
    result = subprocess.run([sys.executable, str(ROOT / 'update_helper_main.py'), '--plan', str(path), '--noninteractive'],
        capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    assert result.returncode != 0
    assert not (path.parent / 'install-result.json').exists()


@pytest.fixture
def cached_recovery(plan):
    path, config = plan
    app = Path(config['app_dir'])
    executable = path.parent / ('updater-' + 'a' * 32 + '.exe')
    executable.write_bytes(b'anonymous copied helper, never executed')
    records = {name: {'sha256': hashlib.sha256(b'anonymous base').hexdigest(), 'size': len(b'anonymous base')}
               for name in REQUIRED_FILES}
    records['WorkBoxUpdater.exe'] = {'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                                   'size': executable.stat().st_size}
    manifest = dict(format=FORMAT, from_tag=OLD, to_tag=NEW, base=records, target=dict(records))
    info = app.stat()
    journal = dict(format='workbox-update-journal-v1', app_dir=str(app),
        app_identity=[info.st_dev, info.st_ino], data_dir=config['data_dir'], phase='applying',
        manifest=manifest, error='', base_zone_hashes={})
    transaction = app / ('.workbox-update-' + 'b' * 32)
    transaction.mkdir()
    (transaction / 'journal.json').write_text(json.dumps(journal), encoding='utf-8')
    return path, config, executable, transaction, journal


@pytest.mark.parametrize('accepted', [False, True])
def test_cached_frozen_helper_locates_recovery_without_exe_package_or_original_pid(cached_recovery, monkeypatch, accepted):
    h = helper()
    path, config, executable, transaction, _ = cached_recovery
    Path(config['app_dir'], 'WorkBox.exe').unlink()
    Path(config['package']).unlink()
    config['parent_pid'] = os.getpid()
    write_plan(path, config)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.setattr(h, '_notify', lambda *args, **kwargs: None)
    monkeypatch.setattr(h, 'wait_for_parent', lambda *args, **kwargs: pytest.fail('恢复定位不等待旧PID'))
    monkeypatch.setattr(h, 'apply_update', lambda *args, **kwargs: pytest.fail('恢复不得重新应用增量包'))
    monkeypatch.setattr(h, 'relaunch', lambda *args: pytest.fail('恢复不得自动打开业务'))
    prompts = []
    monkeypatch.setattr(h, '_ask_recovery', lambda selected: prompts.append(selected) or accepted)
    recovered = []
    monkeypatch.setattr(h, 'recover_update', lambda *args: recovered.append(args) or {'status': 'rolled-back'})
    assert h.main([]) == 0
    assert prompts == [transaction]
    assert recovered == ([(transaction, Path(config['app_dir']))] if accepted else [])
    assert not (path.parent / 'install-result.json').exists()
    assert Path(config['data_dir'], 'workbox.db').read_bytes() == b'private sentinel; not a database'


@pytest.mark.parametrize('missing', ['exe', 'package', 'current-pid'])
def test_normal_plan_does_not_relax_recovery_only_exceptions(cached_recovery, monkeypatch, missing):
    h = helper()
    path, config, _, _, _ = cached_recovery
    if missing == 'exe':
        Path(config['app_dir'], 'WorkBox.exe').unlink()
    elif missing == 'package':
        Path(config['package']).unlink()
    else:
        config['parent_pid'] = os.getpid()
        write_plan(path, config)
    with pytest.raises((OSError, ValueError)):
        h.load_plan(path)
    monkeypatch.setattr(h, 'apply_update', lambda *args, **kwargs: pytest.fail('正常更新不能采用恢复例外'))
    assert h.main(['--plan', str(path), '--noninteractive']) != 0
    assert not (path.parent / 'install-result.json').exists()


@pytest.mark.parametrize('failure', ['wrong-helper-name', 'missing-plan', 'extra-plan-field', 'helper-bytes',
    'from-tag', 'to-tag', 'multiple', 'corrupt', 'corrupt-own-cache'])
def test_cached_recovery_refuses_unbound_or_ambiguous_location(cached_recovery, monkeypatch, failure):
    h = helper()
    path, config, executable, transaction, journal = cached_recovery
    if failure == 'wrong-helper-name':
        target = executable.with_name('updater-' + 'A' * 32 + '.exe')
        executable.rename(target)
        executable = target
    elif failure == 'missing-plan':
        path.unlink()
    elif failure == 'extra-plan-field':
        config['other_app_dir'] = config['app_dir']
        write_plan(path, config)
    elif failure == 'helper-bytes':
        content = executable.read_bytes()
        executable.write_bytes(b'X' + content[1:])
    elif failure in {'from-tag', 'to-tag'}:
        journal['manifest']['from_tag' if failure == 'from-tag' else 'to_tag'] = (
            'v1.0.0.20261113.1' if failure == 'from-tag' else 'v1.0.0.20261113.4')
        (transaction / 'journal.json').write_text(json.dumps(journal), encoding='utf-8')
    elif failure == 'multiple':
        extra = transaction.with_name('.workbox-update-' + 'c' * 32)
        extra.mkdir()
        (extra / 'journal.json').write_text(json.dumps(journal), encoding='utf-8')
    elif failure == 'corrupt':
        (transaction / 'journal.json').write_bytes(b'{broken')
    else:
        own = executable.parent / ('.workbox-update-' + 'd' * 32)
        own.mkdir()
        (own / 'journal.json').write_bytes(b'{broken')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    messages = []
    monkeypatch.setattr(h, '_notify', lambda message, **kwargs: messages.append(message))
    monkeypatch.setattr(h, '_ask_recovery', lambda *args: pytest.fail('不可信恢复位置不能显示确认'))
    monkeypatch.setattr(h, 'recover_update', lambda *args: pytest.fail('不可信恢复位置不能恢复'))
    assert h.main([]) != 0
    assert messages and '保留' in messages[0] and '现场' in messages[0]
    assert Path(config['data_dir'], 'workbox.db').read_bytes() == b'private sentinel; not a database'


def test_installed_helper_recovers_own_transaction_even_when_main_exe_missing(cached_recovery, monkeypatch):
    h = helper()
    _, config, _, transaction, _ = cached_recovery
    app = Path(config['app_dir'])
    Path(app, 'WorkBox.exe').unlink()
    installed = app / 'WorkBoxUpdater.exe'
    installed.write_bytes(b'anonymous installed helper')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(installed))
    monkeypatch.setattr(h, '_notify', lambda *args, **kwargs: None)
    prompts = []
    monkeypatch.setattr(h, '_ask_recovery', lambda selected: prompts.append(selected) or False)
    monkeypatch.setattr(h, 'recover_update', lambda *args: pytest.fail('拒绝确认时不能恢复'))
    assert h.main([]) == 0
    assert prompts == [transaction]


def test_cached_helper_changed_during_confirmation_cannot_recover(cached_recovery, monkeypatch):
    h = helper()
    _, config, executable, _, _ = cached_recovery
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    messages = []
    monkeypatch.setattr(h, '_notify', lambda message, **kwargs: messages.append(message))

    def confirm(_transaction):
        content = executable.read_bytes()
        executable.write_bytes(b'X' + content[1:])
        return True

    monkeypatch.setattr(h, '_ask_recovery', confirm)
    monkeypatch.setattr(h, 'recover_update', lambda *args: pytest.fail('确认后助手变化不得恢复'))
    assert h.main([]) != 0
    assert messages and '摘要' in messages[0]
    assert Path(config['data_dir'], 'workbox.db').read_bytes() == b'private sentinel; not a database'
