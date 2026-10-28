"""运行回归测试；Windows 测试进程记录崩溃，不弹即时调试选择框。"""
from __future__ import annotations
import ctypes
import os
import sys
from pathlib import Path


def main():
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002)
        if kernel.WerSetFlags(0x0020) != 0:
            raise RuntimeError('无法关闭本测试进程的错误报告弹窗，未开始测试。')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import pytest
    return pytest.main(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())
