"""Run the joint QA on a separate Windows desktop with the native Qt windows visible."""

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


_DESKTOP_ACCESS = 0x000F01FF
_CREATE_NO_WINDOW = 0x08000000
_CHILD = '''
import os
from pathlib import Path
import runpy
import sys

log = open(sys.argv[1], 'x', encoding='utf-8', buffering=1)
os.dup2(log.fileno(), 1)
os.dup2(log.fileno(), 2)
sys.stdout = sys.stderr = log
sys.argv = sys.argv[2:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True, help='A new file for the native run log')
    parser.add_argument('verify_args', nargs=argparse.REMAINDER, help='Arguments for verify_unified.py, after --')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('A Windows desktop is required.')
    if not args.verify_args or args.verify_args[0] != '--':
        parser.error('Pass the verify_unified.py arguments after --.')

    import win32event
    import win32process

    log = args.log.resolve()
    if log.exists():
        parser.error('Choose a fresh --log file.')
    log.parent.mkdir(parents=True, exist_ok=True)
    verify = Path(__file__).with_name('verify_unified.py').resolve(strict=True)
    cmd = [sys.executable, '-u', '-B', '-c', _CHILD, str(log), str(verify), *args.verify_args[1:]]
    environment = dict(os.environ, QT_QPA_PLATFORM='windows', QT_QUICK_BACKEND='software', PYTHONUTF8='1')
    ui = ctypes.WinDLL('user32', use_last_error=True)
    ui.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPVOID, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.LPVOID]
    ui.CreateDesktopW.restype = wintypes.HANDLE
    ui.CloseDesktop.argtypes = [wintypes.HANDLE]
    ui.CloseDesktop.restype = wintypes.BOOL
    desktop_name = 'WorkBoxQANative' + uuid4().hex[:12]
    desktop = ui.CreateDesktopW(desktop_name, None, None, 0, _DESKTOP_ACCESS, None)
    if not desktop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = 'winsta0\\' + desktop_name
        process, thread, _, _ = win32process.CreateProcess(
            sys.executable, subprocess.list2cmdline(cmd), None, None, False,
            _CREATE_NO_WINDOW, environment, None, startup)
        thread.Close()
        try:
            win32event.WaitForSingleObject(process, win32event.INFINITE)
            result = win32process.GetExitCodeProcess(process)
        finally:
            process.Close()
    finally:
        if not ui.CloseDesktop(desktop):
            raise ctypes.WinError(ctypes.get_last_error())
    if log.is_file():
        sys.stdout.reconfigure(errors='replace')
        print('\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-20:]))
    return result


if __name__ == '__main__':
    sys.exit(main())
