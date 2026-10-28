"""WorkBox v1.0 desktop entry point and local database health check."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from app import VERSION, WorkBox


def default_data_dir() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "data"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WorkBox 工作箱 v1.0 · 项目收件与 ZIP 持续归档")
    parser.add_argument("--version", action="version", version=f"WorkBox {VERSION}")
    parser.add_argument("--data-dir", type=Path, default=None, help="指定本地数据目录；默认是程序目录下的 data")
    parser.add_argument("--check", action="store_true", help="初始化/检查数据库，输出计数后退出，不打开窗口")
    parser.add_argument("--panel", action="store_true", help="启动时展开管理面板；默认仅显示悬浮球")
    parser.add_argument("--no-tray", action="store_true", help="不用系统托盘，仍可从球右键退出")
    args = parser.parse_args(argv)
    data_dir = args.data_dir if args.data_dir is not None else default_data_dir()
    if args.check:
        try:
            service = WorkBox(data_dir / "workbox.db")
            print(json.dumps({"version": VERSION, "database": str(service.db.path), "counts": service.db.counts()}, ensure_ascii=False))
            return 0
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from desktop_service import DesktopService
    from desktop_controller import FloatingController
    from onboarding_ui import FirstRunDialog

    application = QApplication(sys.argv[:1])
    application.setApplicationName("WorkBox")
    application.setApplicationVersion(VERSION)
    application.setOrganizationName("WorkBox")
    application.setStyle("Fusion")
    application.setQuitOnLastWindowClosed(False)
    controller = None
    try:
        service = DesktopService(data_dir / "workbox.db")
        controller = FloatingController(service, tray_enabled=not args.no_tray)
        controller.exit_requested.connect(application.quit)
        if not controller.start(setup_callback=lambda: FirstRunDialog(service).exec() == QDialog.Accepted):
            return 0
        if args.panel:
            controller.show_panel()
        return application.exec()
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        QMessageBox.critical(None, "WorkBox 无法启动", f"无法打开本地数据。请确认程序所在目录可以写入。\n\n{exc}\n\n未重置或覆盖原有数据。")
        return 1
    finally:
        if controller is not None:
            controller.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
