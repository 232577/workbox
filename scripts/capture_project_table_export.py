"""Capture real Qt controls with an isolated, anonymous project fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService
from theme import current_mode, set_theme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    previous = current_mode()
    evidence = {'fixture': 'anonymous temporary database; no user data', 'renders': []}
    with tempfile.TemporaryDirectory(prefix='qt-fixture-', dir=args.output) as temporary:
        service = DesktopService(Path(temporary) / 'data' / 'workbox.db')
        result = service.receive_text('\n'.join(f'{i}. 匿名材料核验项目{i:02d}' for i in range(1, 13)))
        with service.db.connect() as connection:
            connection.execute('DELETE FROM project_type_links')
            for pid in result['project_ids']:
                connection.execute('UPDATE projects SET created_at=? WHERE id=?',
                    ('2026-11-05T16:30:00Z' if pid <= 8 else '2026-11-06T16:30:00Z', pid))
        service.set_status(1, '待交付')
        service.set_status(2, '开发中')
        panel = CompactPanel(service)
        try:
            panel.show()
            panel.status_buttons['待开发'].click()
            panel.added_date_filter.setCurrentIndex(panel.added_date_filter.findData('2026-11-06'))
            panel.type_filter.setCurrentIndex(panel.type_filter.findData(0))
            for mode in ('light', 'dark'):
                set_theme(mode)
                for width, height in ((1200, 780), (660, 480)):
                    panel.resize(width, height)
                    QTest.qWait(180)
                    app.processEvents()
                    name = f'project-table-{mode}-{width}.png'
                    assert panel.grab().save(str(args.output / name))
                    assert panel.project_export_button.isVisible() and panel.type_filter.isVisible()
                    assert panel.width() == width and panel.height() == height
                    evidence['renders'].append(dict(file=name, width=width, height=height,
                        current_count=panel.project_list.count(), date=panel.added_date_filter.currentText(),
                        date_description=panel.added_date_filter.accessibleDescription(),
                        state='待开发', project_type=panel.type_filter.currentText()))
                    panel.project_export_menu.popup(panel.project_export_button.mapToGlobal(
                        QPoint(0, panel.project_export_button.height())))
                    QTest.qWait(80)
                    assert panel.project_export_menu.isVisible()
                    menu_name = f'project-table-menu-{mode}-{width}.png'
                    assert panel.project_export_menu.grab().save(str(args.output / menu_name))
                    evidence['renders'][-1]['menu_file'] = menu_name
                    evidence['renders'][-1]['menu_actions'] = [action.text() for action in panel.project_export_menu.actions()
                                                              if action.isVisible() and not action.isSeparator()]
                    panel.project_export_menu.hide()
            panel.search.setText('匿名无匹配结果')
            QTest.qWait(100)
            assert panel.empty_clear_filters_button.isVisible()
            assert panel.grab().save(str(args.output / 'project-table-empty-dark-660.png'))
            panel.empty_clear_filters_button.click()
            assert panel.project_list.count() == 12
            evidence['empty_reset_count'] = panel.project_list.count()
        finally:
            panel.document_pane.shutdown()
            panel.hide()
            panel.deleteLater()
            app.processEvents()
            set_theme(previous)
    (args.output / 'capture-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False))


if __name__ == '__main__':
    main()
