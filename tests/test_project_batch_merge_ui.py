"""Original creation-batch filtering survives an explicitly confirmed file-project merge."""
import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from compact_ui import CompactPanel
from desktop_service import DesktopService
from fluent_views import PROJECT_BATCH_MARKERS_ROLE
from parser import ParsedProject


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='实际项目合并目录身份租约使用 Windows API')


def visible_ids(panel):
    return tuple(panel.project_list.item(index).data(Qt.UserRole)
                 for index in range(panel.project_list.count()))


def test_original_batch_survives_file_intake_and_confirmed_merge_with_combined_filters(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    service = DesktopService(tmp_path / 'data' / 'workbox.db')
    captured = service.capture_message('匿名原始清单', '匿名最初批次',
        [ParsedProject('匿名保留项目系统'), ParsedProject('匿名同批第二工具')])
    target, sibling = captured['project_ids']
    batch_id = service.get_project(target)['batch_id']
    original_key = f'batch:{batch_id}'
    with service.db.connect() as connection:
        connection.execute('UPDATE projects SET created_at=? WHERE id=?', ('2026-11-05T16:30:00Z', target))
        connection.execute('UPDATE projects SET created_at=? WHERE id=?', ('2026-11-06T16:30:00Z', sibling))
    panel = CompactPanel(service)
    panel.resize(1200, 780)
    panel.show()
    app.processEvents()
    try:
        index = panel.batch_filter.findData(original_key)
        assert index >= 0
        panel.batch_filter.setCurrentIndex(index)
        assert set(visible_ids(panel)) == {target, sibling}
        assert panel.batch_progress_summary.text() == '待完成2，共2'

        source = tmp_path / '匿名误建名称_操作手册.txt'
        source.write_text('匿名独立文件内容，保留原始文件名。', encoding='utf-8')
        intake = service.receive_path(source)
        source_id = intake['file']['project_id']
        assert source_id not in (target, sibling)
        panel.refresh()
        assert panel.batch_filter.currentData() == original_key
        assert set(visible_ids(panel)) == {target, sibling}
        assert any(marker['source_kind'] == 'receipt'
                   for row in service.project_workbench_snapshot()['projects'] if row['id'] == source_id
                   for marker in row['batch_markers'])

        plan = service.preview_project_rename(source_id, '匿名保留项目系统')
        assert plan['kind'] == 'merge' and plan['target']['id'] == target
        assert service.get_project(source_id)['merged_into_id'] is None
        result = service.rename_project(source_id, '匿名保留项目系统', expected_token=plan['token'],
            merge_target_id=target, confirmed_merge=True)
        assert result['project_id'] == target and service.get_project(source_id)['merged_into_id'] == target
        assert source.read_text(encoding='utf-8') == '匿名独立文件内容，保留原始文件名。'

        panel.refresh()
        app.processEvents()
        assert panel.batch_filter.findData(original_key) >= 0
        assert panel.batch_filter.currentData() == original_key
        assert set(visible_ids(panel)) == {target, sibling}
        assert len(visible_ids(panel)) == 2 and source_id not in visible_ids(panel)
        assert panel.batch_progress_summary.text() == '待完成1，共2'
        assert '待交付1，已交付0' in panel.batch_filter.accessibleDescription()
        retained_item = next(panel.project_list.item(i) for i in range(panel.project_list.count())
                             if panel.project_list.item(i).data(Qt.UserRole) == target)
        markers = retained_item.data(PROJECT_BATCH_MARKERS_ROLE)
        assert original_key in {marker['key'] for marker in markers}
        assert any(marker['source_kind'] == 'receipt' for marker in markers)
        original = next(marker for marker in markers if marker['key'] == original_key)
        assert (original['total_count'], original['remaining_count'], original['pending_delivery_count'],
                original['delivered_count']) == (2, 2, 1, 0)
        assert service.get_project(target)['batch_id'] == batch_id

        panel.added_date_filter.setCurrentIndex(panel.added_date_filter.findData('2026-11-06'))
        assert visible_ids(panel) == (target,)
        panel.status_buttons['待交付'].click()
        assert visible_ids(panel) == (target,)
        assert panel.batch_filter.currentData() == original_key
        assert panel.batch_progress_summary.text() == '待完成1，共2'
        panel.status_buttons['待开发'].click()
        assert visible_ids(panel) == () and panel.empty.isVisible()
        assert panel.batch_progress_summary.text() == '待完成1，共2'
        panel.added_date_filter.setCurrentIndex(panel.added_date_filter.findData('2026-11-07'))
        assert visible_ids(panel) == (sibling,)
        assert panel.batch_filter.currentData() == original_key
        assert panel.batch_progress_summary.text() == '待完成1，共2'
    finally:
        panel.document_pane.shutdown()
        panel.hide()
        panel.deleteLater()
        app.processEvents()
