"""Anonymous full-installer fixtures; never use an installed application or database."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from workbox.updates.delta_format import REQUIRED_FILES
from workbox.updates.incremental_apply import runtime_guard, verify_tree


def module():
    return importlib.import_module('workbox.updates.installer_runtime')


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def anonymous_process_inventory(monkeypatch):
    # Process scenarios below use ordinary mock image paths. Real old-app launch
    # and installer rejection are verified separately on disposable Windows CI.
    monkeypatch.setattr(module(), '_workbox_process_images', lambda: [], raising=False)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    app = tmp_path / 'app'
    payload = {name: ('anonymous current ' + name).encode() for name in REQUIRED_FILES
               if name != 'PACKAGE_CONTENTS.sha256'}
    payload['licenses/anonymous.txt'] = b'anonymous license'
    payload['PACKAGE_CONTENTS.sha256'] = ''.join(
        f'{sha(data)}  {name}\n' for name, data in sorted(payload.items())).encode()
    legacy = {'_internal/old-a.dll': b'anonymous old a',
              '_internal/nested/old-b.dll': b'anonymous old b'}
    for name, data in {**payload, **legacy}.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(module(), 'KNOWN_OBSOLETE', {name: frozenset({sha(data)})
                                                   for name, data in legacy.items()})
    # These bytes are intentionally not a real database or real user materials.
    sentinels = {'data/anonymous.db': b'not a database',
                 'materials/anonymous.txt': b'not user materials',
                 'personal.txt': b'owner root bytes', 'unins000.exe': b'uninstaller bytes'}
    for name, data in sentinels.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # Model an existing installation's runtime coordination file before byte snapshots.
    with runtime_guard(app):
        pass
    records = {name: {'sha256': sha(data), 'size': len(data)} for name, data in payload.items()}
    return app, payload, legacy, sentinels, records


def snapshot(app):
    return {p.relative_to(app).as_posix(): p.read_bytes() for p in app.rglob('*') if p.is_file()}


def finalize(candidate):
    app, payload, *_ = candidate
    return module().finalize_full_install(app, sha(payload['PACKAGE_CONTENTS.sha256']))


def preflight(candidate):
    app, payload, *_ = candidate
    return module().preflight_full_install(app, sha(payload['PACKAGE_CONTENTS.sha256']))


def test_known_obsolete_files_are_preserved_and_exact_baseline_is_restored(candidate):
    app, payload, legacy, sentinels, records = candidate
    identities = {name: (app / name).stat().st_ino for name in legacy}
    assert preflight(candidate)['status'] == 'ready'
    result = finalize(candidate)
    assert result['status'] == 'verified' and result['retired'] == 2
    recovery = app / result['recovery_dir']
    assert recovery.parent == app / '.workbox-runtime-history'
    for name, data in legacy.items():
        assert not (app / name).exists()
        assert (recovery / 'files' / name).read_bytes() == data
        assert (recovery / 'files' / name).stat().st_ino == identities[name]
    plan = json.loads((recovery / 'plan.json').read_text(encoding='utf-8'))
    receipt = json.loads((recovery / 'receipt.json').read_text(encoding='utf-8'))
    assert plan['manifest_sha256'] == sha(payload['PACKAGE_CONTENTS.sha256'])
    assert receipt['plan_sha256'] == sha((recovery / 'plan.json').read_bytes())
    assert set(plan['files']) == set(legacy)
    verify_tree(app, records)
    for name, data in sentinels.items():
        assert (app / name).read_bytes() == data
    retained = snapshot(recovery)
    assert finalize(candidate) == {'status': 'verified', 'retired': 0, 'recovery_dir': None}
    assert snapshot(recovery) == retained
    assert len(list(recovery.parent.iterdir())) == 1


@pytest.mark.parametrize('extra,content', [('_internal/unknown.dll', b'unknown'),
                                         ('_internal/old-a.dll', b'changed legacy bytes')])
def test_unknown_or_changed_extras_block_all_moves(candidate, extra, content):
    app, *_ = candidate
    (app / extra).write_bytes(content)
    before = snapshot(app)
    with pytest.raises(ValueError):
        finalize(candidate)
    assert snapshot(app) == before
    assert not (app / '.workbox-runtime-history').exists()


@pytest.mark.parametrize('damage', ['wrong-pin', 'changed-current', 'missing-current',
                                    'bad-format', 'duplicate', 'private-path', 'missing-helper'])
def test_bad_manifest_or_current_payload_never_retires(candidate, damage):
    app, payload, *_ = candidate
    pin = sha(payload['PACKAGE_CONTENTS.sha256'])
    manifest = app / 'PACKAGE_CONTENTS.sha256'
    if damage == 'wrong-pin':
        pin = '0' * 64
    elif damage == 'changed-current':
        (app / 'WorkBox.exe').write_bytes(b'changed current')
    elif damage == 'missing-current':
        (app / 'WorkBox.exe').unlink()
    else:
        text = manifest.read_text(encoding='utf-8')
        if damage == 'bad-format':
            text = 'malformed\n'
        elif damage == 'duplicate':
            text += text.splitlines()[0] + '\n'
        elif damage == 'private-path':
            text += '0' * 64 + '  data/anonymous.db\n'
        elif damage == 'missing-helper':
            text = ''.join(line + '\n' for line in text.splitlines() if not line.endswith('WorkBoxUpdater.exe'))
        manifest.write_text(text, encoding='utf-8')
        pin = sha(manifest.read_bytes())
    before = snapshot(app)
    with pytest.raises((ValueError, OSError)):
        module().finalize_full_install(app, pin)
    assert snapshot(app) == before
    assert not (app / '.workbox-runtime-history').exists()


def test_fresh_target_preflight_does_not_create_it(tmp_path):
    app = tmp_path / 'missing' / 'app'
    assert module().preflight_full_install(app, 'a' * 64) == {'status': 'ready'}
    assert not app.parent.exists()
    with pytest.raises(ValueError):
        module().preflight_full_install(Path('relative-app'), 'a' * 64)


def test_incremental_journal_and_active_runtime_block_preflight_and_finalize(candidate):
    app, *_ = candidate
    with runtime_guard(app):
        with pytest.raises((OSError, RuntimeError)):
            preflight(candidate)
        with pytest.raises((OSError, RuntimeError)):
            finalize(candidate)
    pending = app / ('.workbox-update-' + 'a' * 32)
    pending.mkdir()
    (pending / 'journal.json').write_text('{}', encoding='utf-8')
    before = snapshot(app)
    for action in (lambda: preflight(candidate), lambda: finalize(candidate)):
        with pytest.raises((ValueError, RuntimeError)):
            action()
        assert snapshot(app) == before


def test_interrupted_move_resumes_same_plan_without_overwrites(candidate, monkeypatch):
    app, _, legacy, _, records = candidate
    real_move = module().move_checked
    calls = []
    def interrupted(source, destination, expected, **options):
        plan, = (app / '.workbox-runtime-history').glob('*/plan.json')
        assert json.loads(plan.read_text(encoding='utf-8'))['files']
        calls.append(source)
        if len(calls) == 2:
            raise OSError('anonymous ordinary I/O interruption')
        return real_move(source, destination, expected, **options)
    monkeypatch.setattr(module(), 'move_checked', interrupted)
    with pytest.raises(OSError, match='ordinary I/O interruption'):
        finalize(candidate)
    recovery, = (app / '.workbox-runtime-history').iterdir()
    original_plan = (recovery / 'plan.json').read_bytes()
    assert not (recovery / 'receipt.json').exists()
    assert sum((app / name).exists() for name in legacy) == 1
    assert preflight(candidate)['status'] == 'ready'
    monkeypatch.setattr(module(), 'move_checked', real_move)
    assert finalize(candidate)['retired'] == 2
    assert (recovery / 'plan.json').read_bytes() == original_plan
    assert len(list(recovery.parent.iterdir())) == 1
    verify_tree(app, records)


def test_clean_install_needs_no_history(candidate):
    app, _, legacy, _, records = candidate
    for name in legacy:
        (app / name).unlink()
    assert finalize(candidate) == {'status': 'verified', 'retired': 0, 'recovery_dir': None}
    assert not (app / '.workbox-runtime-history').exists()
    verify_tree(app, records)


def interrupt_after_first_move(candidate, monkeypatch):
    app, *_ = candidate
    original = module().move_checked
    calls = []
    def interrupt(source, destination, expected, **options):
        calls.append(source)
        if len(calls) == 2:
            raise OSError('anonymous interruption')
        return original(source, destination, expected, **options)
    with monkeypatch.context() as patch:
        patch.setattr(module(), 'move_checked', interrupt)
        with pytest.raises(OSError, match='anonymous interruption'):
            finalize(candidate)
    recovery, = (app / '.workbox-runtime-history').iterdir()
    return recovery


@pytest.mark.parametrize('damage', ['original-changed', 'saved-changed', 'both-locations',
                                    'missing-saved', 'unknown-history-file', 'malformed-plan',
                                    'different-app', 'different-manifest', 'second-pending'])
def test_pending_recovery_conflicts_stop_before_any_further_moves(candidate, monkeypatch, damage):
    app, payload, legacy, *_ = candidate
    recovery = interrupt_after_first_move(candidate, monkeypatch)
    moved_name, remaining_name = sorted(legacy)
    original = app / remaining_name
    saved = recovery / 'files' / moved_name
    if damage == 'original-changed':
        original.write_bytes(b'unknown changed original')
    elif damage == 'saved-changed':
        saved.write_bytes(b'unknown changed saved bytes')
    elif damage == 'both-locations':
        (app / moved_name).write_bytes(legacy[moved_name])
    elif damage == 'missing-saved':
        saved.unlink()
    elif damage == 'unknown-history-file':
        (recovery / 'files/_internal/unknown.dll').write_bytes(b'unknown history')
    elif damage == 'malformed-plan':
        (recovery / 'plan.json').write_text('{}', encoding='utf-8')
    elif damage in ('different-app', 'different-manifest'):
        path = recovery / 'plan.json'
        plan = json.loads(path.read_text(encoding='utf-8'))
        if damage == 'different-app':
            plan['app_identity'][1] += 1
        else:
            plan['manifest_sha256'] = '0' * 64
        path.write_text(json.dumps(plan), encoding='utf-8')
    elif damage == 'second-pending':
        another = recovery.parent / ('a' * 32)
        another.mkdir()
        (another / 'plan.json').write_bytes((recovery / 'plan.json').read_bytes())
    before = snapshot(app)
    with pytest.raises((ValueError, OSError)):
        finalize(candidate)
    assert snapshot(app) == before
    with pytest.raises((ValueError, OSError)):
        preflight(candidate)
    assert snapshot(app) == before


def test_retry_preflights_saved_files_before_moving_remaining_original(candidate, monkeypatch):
    app, _, legacy, *_ = candidate
    recovery = interrupt_after_first_move(candidate, monkeypatch)
    moved_name, remaining_name = sorted(legacy)
    (recovery / 'files' / moved_name).write_bytes(b'changed saved bytes')
    before = snapshot(app)
    with pytest.raises(ValueError):
        finalize(candidate)
    assert snapshot(app) == before
    assert (app / remaining_name).is_file()


@pytest.mark.parametrize('name', ['plan.json', 'receipt.json'])
def test_unknown_recovery_records_are_not_overwritten(candidate, name):
    app, *_ = candidate
    recovery = app / '.workbox-runtime-history' / ('b' * 32)
    recovery.mkdir(parents=True)
    (recovery / name).write_bytes(b'unknown record')
    before = snapshot(app)
    with pytest.raises(ValueError):
        finalize(candidate)
    assert snapshot(app) == before


def test_receipt_write_interruption_resumes_without_moving_files_again(candidate, monkeypatch):
    app, _, _, _, records = candidate
    original = module()._write_new_json
    def interrupt(path, value):
        if Path(path).name == 'receipt.json':
            raise OSError('anonymous receipt interruption')
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(module(), '_write_new_json', interrupt)
        with pytest.raises(OSError, match='anonymous receipt interruption'):
            finalize(candidate)
    verify_tree(app, records)
    recovery, = (app / '.workbox-runtime-history').iterdir()
    retained = snapshot(recovery)
    original_move = module().move_checked
    def no_more_moves(*args, **kwargs):
        if Path(args[1]).name == 'receipt.json':
            return original_move(*args, **kwargs)
        pytest.fail('all payload already retired; retry must only verify and write receipt')
    monkeypatch.setattr(module(), 'move_checked', no_more_moves)
    assert finalize(candidate)['retired'] == 2
    assert all((recovery / name).read_bytes() == data for name, data in retained.items())
    assert (recovery / 'receipt.json').is_file()


def test_retry_enforces_combined_current_and_saved_size_limit(candidate, monkeypatch):
    app, _, legacy, _, records = candidate
    interrupt_after_first_move(candidate, monkeypatch)
    total = sum(record['size'] for record in records.values()) + sum(map(len, legacy.values()))
    monkeypatch.setattr(module().fmt, 'MAX_TOTAL_SIZE', total - 1)
    before = snapshot(app)
    with pytest.raises(ValueError, match='总长度'):
        finalize(candidate)
    assert snapshot(app) == before


def test_malformed_plan_hash_has_a_structured_error(candidate, monkeypatch):
    app, *_ = candidate
    recovery = interrupt_after_first_move(candidate, monkeypatch)
    path = recovery / 'plan.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    next(iter(plan['files'].values()))['sha256'] = ['malformed hash']
    path.write_text(json.dumps(plan), encoding='utf-8')
    before = snapshot(app)
    with pytest.raises(ValueError):
        finalize(candidate)
    assert snapshot(app) == before


def test_preflight_blocks_other_build_before_any_install_copy(candidate, monkeypatch):
    app, *_ = candidate
    interrupt_after_first_move(candidate, monkeypatch)
    before = snapshot(app)
    with pytest.raises(ValueError, match='另一程序版本'):
        module().preflight_full_install(app, '0' * 64)
    assert snapshot(app) == before


@pytest.mark.parametrize('pin', [None, '', 'A' * 64, 'a' * 63, 0])
@pytest.mark.parametrize('existing', [False, True])
def test_preflight_rejects_malformed_pin_before_root_or_lock_mutations(tmp_path, pin, existing):
    app = tmp_path / 'target' / 'app'
    if existing:
        app.mkdir(parents=True)
    with pytest.raises(ValueError, match='SHA-256'):
        module().preflight_full_install(app, pin)
    if existing:
        assert list(app.iterdir()) == []
    else:
        assert not app.parent.exists()


@pytest.mark.parametrize('stage', ['plan', 'receipt'])
def test_partial_metadata_write_keeps_all_legacy_bytes_and_allows_retry(candidate, monkeypatch, stage):
    app, _, legacy, _, records = candidate
    original = module().json.dump
    expected_format = module().PLAN_FORMAT if stage == 'plan' else module().RECEIPT_FORMAT
    calls = []
    def partial_write(value, stream, **options):
        if value.get('format') == expected_format:
            calls.append(1)
            stream.write('{')
            raise OSError('anonymous partial metadata write')
        return original(value, stream, **options)
    with monkeypatch.context() as patch:
        patch.setattr(module().json, 'dump', partial_write)
        with pytest.raises(OSError, match='anonymous partial metadata write'):
            finalize(candidate)
    assert calls == [1]
    history = app / '.workbox-runtime-history'
    incomplete = {path: path.read_bytes() for path in history.rglob('*')
                  if path.is_file() and path.read_bytes() == b'{'}
    assert len(incomplete) == 1
    # Every old file still exists exactly once, either in place or in its registered history.
    for name, data in legacy.items():
        locations = [path for path in [app / name, *history.glob('*/files/' + name)] if path.exists()]
        assert len(locations) == 1
        assert locations[0].read_bytes() == data
    result = finalize(candidate)
    assert result['status'] == 'verified' and result['retired'] == 2
    verify_tree(app, records)
    for path, data in incomplete.items():
        assert path.read_bytes() == data


def test_preflight_blocks_legacy_app_without_new_runtime_lock(tmp_path, monkeypatch):
    app = tmp_path / 'anonymous-install'
    app.mkdir()
    (app / 'WorkBox.exe').write_bytes(b'anonymous legacy executable placeholder')
    monkeypatch.setattr(module(), '_workbox_process_images', lambda: [str(app / 'WorkBox.exe')])
    before = snapshot(app)
    with pytest.raises(RuntimeError, match='退出 WorkBox'):
        module().preflight_full_install(app, 'a' * 64)
    assert snapshot(app) == before
    assert not (app / '.workbox-update.lock').exists()
    assert not (app / '.workbox-runtime-history').exists()


def test_preflight_ignores_workbox_running_from_other_anonymous_directory(tmp_path, monkeypatch):
    app, other = tmp_path / 'anonymous-install', tmp_path / 'other-anonymous-install'
    for folder in (app, other):
        folder.mkdir()
        (folder / 'WorkBox.exe').write_bytes(b'anonymous executable placeholder')
    monkeypatch.setattr(module(), '_workbox_process_images', lambda: [str(other / 'WorkBox.exe')])
    assert module().preflight_full_install(app, 'a' * 64) == {'status': 'ready'}
    assert (app / 'WorkBox.exe').read_bytes() == b'anonymous executable placeholder'
    assert (other / 'WorkBox.exe').read_bytes() == b'anonymous executable placeholder'


def test_preflight_stops_on_unavailable_candidate_image_query_without_lock(tmp_path, monkeypatch):
    app = tmp_path / 'anonymous-install'
    app.mkdir()
    (app / 'WorkBox.exe').write_bytes(b'anonymous executable placeholder')
    def unavailable():
        raise OSError(5, 'anonymous process image query unavailable')
    monkeypatch.setattr(module(), '_workbox_process_images', unavailable)
    before = snapshot(app)
    with pytest.raises(OSError, match='query unavailable'):
        module().preflight_full_install(app, 'a' * 64)
    assert snapshot(app) == before
    assert not (app / '.workbox-update.lock').exists()
