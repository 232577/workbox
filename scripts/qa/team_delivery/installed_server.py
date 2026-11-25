"""Own a fresh loopback Kanboard v11 server and remove its anonymous state after clean stop."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess


def port_pair():
    with socket.socket() as web, socket.socket() as fastcgi:
        web.bind(('127.0.0.1', 0))
        fastcgi.bind(('127.0.0.1', 0))
        return web.getsockname()[1], fastcgi.getsockname()[1]


def run(program, state, action, *arguments, stdin=None, timeout=120):
    executable = program / 'python312/python.exe'
    script = program / 'scripts/kanboard_runtime.py'
    result = subprocess.run([str(executable), '-I', '-S', '-B', str(script), action,
                             '--state-root', str(state), *arguments],
                            cwd=program, input=stdin, encoding='utf-8', capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'The anonymous installed server could not {action}. {result.stdout.strip()}')
    return json.loads(result.stdout)


def assert_closed(port):
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(('127.0.0.1', port)) != 0, f'Anonymous port {port} is still reachable.'


def remove_owned_directory(path, owner):
    owner = owner.resolve(strict=True)
    if path.resolve(strict=True).parent != owner or path.resolve(strict=True) != path:
        raise RuntimeError('Refusing to remove a path outside this anonymous run.')

    def remove_readonly_copy(operation, filename, exc_info):
        removed = Path(filename)
        error = exc_info[1]
        if (operation is not os.unlink or not isinstance(error, PermissionError)
                or not removed.is_relative_to(path) or removed.resolve(strict=True) != removed):
            raise error
        info = removed.stat(follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not info.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY):
            raise error
        # Filee protects its scratch copy. Change only an unaliased file that
        # belongs to the already drained anonymous run being removed.
        removed.chmod(stat.S_IREAD | stat.S_IWRITE)
        removed.unlink()

    shutil.rmtree(path, onerror=remove_readonly_copy)
    assert not path.exists()


@contextmanager
def installed_server(program, output, evidence, worker_parent=None):
    program = program.resolve(strict=True)
    output = output.resolve(strict=True)
    state = output / 'server-state'
    assert not state.exists(), 'Use a fresh directory for each anonymous acceptance run.'
    if worker_parent is None:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise ValueError('Pass a short directory with --worker-parent.')
        worker_parent = Path(local)
    worker_parent = worker_parent.resolve(strict=True)
    worker = worker_parent / ('wbq-' + secrets.token_hex(5))
    assert not worker.exists()
    http, fastcgi = port_pair()
    launch_attempted = False
    running = None
    final = None
    business_completed = False
    secret = secrets.token_hex(24)
    try:
        # Preparation reads and verifies every file of the restored bundle.
        prepared = run(program, state, 'prepare-installed', '--bind-address', '127.0.0.1',
                       '--http-port', str(http), '--fastcgi-port', str(fastcgi), '--worker-root', str(worker),
                       timeout=420)
        assert prepared['state'] == 'prepared'
        manifest = json.loads((state / 'manifest.json').read_text(encoding='utf-8'))
        assert Path(manifest['worker']['work_root']).resolve(strict=True) == worker
        run(program, state, 'initialize', '--username', 'admin', '--password-stdin', stdin=secret + '\n')
        launch_attempted = True
        running = run(program, state, 'start')
        assert running['state'] == 'running' and running['worker']['private_ready']
        evidence.update(server_url=f'http://127.0.0.1:{http}/', server_source_commit=running['source_commit'],
                        worker_started=True, newly_initialized=True)
        yield state, secret
        business_completed = True
    finally:
        if launch_attempted:
            stopped = run(program, state, 'stop')
            final = run(program, state, 'status')
            assert stopped['state'] == final['state'] and final['state'] in ('stopped', 'failed'), final['state']
            assert final['stage'] == 'owned_jobs_confirmed_empty'
            if running is not None:
                assert final['run_id'] == running['run_id']
            assert final['writer_registry']['active_owner_count'] == final['writer_registry']['pending_creations'] == 0
            assert_closed(http)
            assert_closed(fastcgi)
            evidence.update(server_terminal_state=final['state'], server_stopped=True,
                            owned_processes_drained=True, ports_closed=True)
            if final['state'] == 'failed':
                observations = worker / final['worker']['runner_id'] / 'observations.jsonl'
                if observations.is_file():
                    lines = observations.read_text(encoding='utf-8').splitlines()
                    if lines:
                        evidence['worker_last_observation'] = json.loads(lines[-1])
        if state.exists():
            remove_owned_directory(state, output)
            evidence['server_state_removed'] = True
        if worker.exists():
            remove_owned_directory(worker, worker_parent)
            evidence['worker_state_removed'] = True
        if final is not None and final['state'] == 'failed' and business_completed:
            raise RuntimeError('The anonymous business checks passed, but the owned server failed before orderly stop.')
