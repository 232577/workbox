"""A helper exit code alone cannot prove a windowless PS script executed."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows process creation contract')


def test_launch_uses_hidden_console_and_independent_restart_environment(monkeypatch, tmp_path):
    import update_install
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(update_install, 'handoff_command', lambda *a: ['powershell.exe', 'anonymous'])
    received = {}
    def popen(command, **kwargs):
        received.update(command=command, **kwargs)
        return object()
    monkeypatch.setattr(subprocess, 'Popen', popen)
    before = dict(os.environ)
    update_install.launch_installer(tmp_path/'unused.exe', object(), tmp_path/'data')
    assert not received['creationflags'] & subprocess.DETACHED_PROCESS
    assert received['creationflags'] & subprocess.CREATE_NO_WINDOW
    assert received['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert received['stdin'] == received['stdout'] == received['stderr'] == subprocess.DEVNULL
    assert received['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert dict(os.environ) == before


def test_real_windowless_parent_helper_runs_after_parent_exit(tmp_path):
    """No installer: run an actual harmless PowerShell child through production spawn."""
    root = Path(__file__).resolve().parents[1]
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    assert pythonw.is_file(), 'Windows test runtime must include pythonw'
    marker = tmp_path/'helper-entered.json'
    runner = tmp_path/'windowless_parent.py'
    runner.write_text('''from pathlib import Path
import base64, json, os, sys
sys.path.insert(0, sys.argv[1])
import update_install
sys.frozen = True
marker = str(Path(sys.argv[2]).resolve()).replace("'", "''")
script = "$p=$null;try{$p=[Diagnostics.Process]::GetProcessById("+str(os.getpid())+")}catch [ArgumentException]{};if($null -ne $p -and -not $p.WaitForExit(15000)){exit 2};[IO.File]::WriteAllText('"+marker+"','{\\"executed_after_parent_exit\\":true}')"
ps = str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
command = [ps,'-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()]
update_install.handoff_command = lambda *args: command
update_install.launch_installer(Path('unused.exe'), object(), Path('unused-data'))
''', encoding='utf-8')
    parent = subprocess.run([str(pythonw), str(runner), str(root), str(marker)], cwd=tmp_path, timeout=25)
    assert parent.returncode == 0
    end = time.monotonic()+20
    while not marker.exists() and time.monotonic() < end:
        time.sleep(.1)
    assert marker.is_file(), 'Windowless helper returned without executing its script'
    assert json.loads(marker.read_text()) == {'executed_after_parent_exit': True}


def test_handoff_records_entry_before_waiting_for_application(tmp_path):
    from update_client import Release
    from update_install import handoff_command
    data=b'MZanonymous command-generation fixture'
    installer=tmp_path/'WorkBox-anonymous.exe';installer.write_bytes(data)
    app=tmp_path/'app';app.mkdir();(app/'WorkBox.exe').write_bytes(b'anonymous')
    release=Release('v1.0.0.20991231',installer.name,len(data),hashlib.sha256(data).hexdigest(),
                    'https://github.com/232577/workbox/releases/download/v1.0.0.20991231/'+installer.name,'')
    script=base64.b64decode(handoff_command(installer,release,app,1234)[-1]).decode('utf-16le')
    assert "status='helper-started'" in script
    assert script.index("status='helper-started'") < script.index('$parent.WaitForExit')


@pytest.mark.parametrize('exit_code', [5, 7])
def test_incomplete_install_notifies_user_and_keeps_exit_code(tmp_path, exit_code):
    from update_client import Release
    from update_install import handoff_command

    content = b'MZanonymous installer fixture'
    installer = tmp_path / 'WorkBox-anonymous.exe'
    installer.write_bytes(content)
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'WorkBox.exe').write_bytes(b'anonymous installed executable')
    release = Release('v1.0.0.20991231', installer.name, len(content), hashlib.sha256(content).hexdigest(), '', '')
    command = handoff_command(installer, release, app, 2147483647)
    script = base64.b64decode(command[-1]).decode('utf-16le')
    # Run the real helper. Substitute a harmless process for Setup and record its
    # user notification without opening a dialog that requires a human to dismiss it.
    substitute = r'''
function Start-Process {
  param([string]$FilePath, [string]$ArgumentList, [string]$WorkingDirectory, [switch]$PassThru)
  if ($FilePath -ne $cfg.installer) { throw 'Unexpected application launch.' }
  Microsoft.PowerShell.Management\Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -ArgumentList '/d /c exit __EXIT__' -PassThru -WindowStyle Hidden
}
'''.replace('__EXIT__', str(exit_code))
    script = re.sub(r'(?m)^ +Add-Type -AssemblyName System.Windows.Forms\n', '', script)
    script, replaced = re.subn(r'(?m)^( +)\[Windows.Forms.MessageBox\]::Show\(.*$',
        lambda match: match[1] + "[IO.File]::WriteAllText($cfg.result + '.notice', 'notified')", script)
    assert replaced == 1
    command[-1] = base64.b64encode((substitute + script).encode('utf-16le')).decode()
    run = subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW,
                         capture_output=True, timeout=25)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / 'install-result.json.notice').is_file()
    result = json.loads((tmp_path / 'install-result.json').read_text(encoding='utf-8-sig'))
    assert result['status'] == 'failed' and result['exit_code'] == exit_code
    assert 'Unexpected application launch.' not in result['error']
    assert installer.read_bytes() == content
