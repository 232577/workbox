"""归档分类比较方式的真实Qt表单保存与预览契约；仅匿名库。"""
import pytest
from PySide6.QtWidgets import QApplication

from archive_classification_ui import ArchiveClassificationPane
from desktop_service import DesktopService


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def settle(app, page):
    for _ in range(8):
        if page.worker:
            assert page.worker.wait(10000)
        app.processEvents()
        if page.worker is None:
            return
    raise AssertionError('分类后台任务未结束')


def test_fresh_exact_rule_is_explicit_and_returns_to_projects(app, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    page = ArchiveClassificationPane(service)
    try:
        page.refresh(); settle(app, page)
        assert page.comparison.currentData() == 'exact'
        assert page.minimum_files.value() == 16
        assert page.low_label.text() == '其他已知数量'
        assert page.return_button.text() == '返回项目'
        assert not page.future_enabled.isChecked()
    finally:
        page.shutdown(); page.close()


def test_old_rule_is_displayed_without_silent_conversion_and_can_explicitly_change(app, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.archive_classification.set_policy(True, 7)
    page = ArchiveClassificationPane(service)
    try:
        page.refresh(); settle(app, page)
        assert page.comparison.currentData() == 'at_least'
        assert page.minimum_files.value() == 7
        assert page.low_label.text() == '文件数 0—6'
        page.preview_existing(); settle(app, page)
        page.comparison.setCurrentIndex(page.comparison.findData('exact'))
        assert not page.preview_button.isEnabled() and page._preview is None
        assert service.archive_classification.policy()['comparison'] == 'at_least'
        page.minimum_files.setValue(16)
        page.save_policy(); settle(app, page)
        policy = service.archive_classification.policy()
        assert policy['comparison'] == 'exact' and policy['minimum_files'] == 16 and policy['enabled']
        assert page.low_label.text() == '其他已知数量'
        assert page.preview_button.isEnabled()
    finally:
        page.shutdown(); page.close()


def test_busy_and_refresh_preserve_unsaved_comparison_and_cancel_is_read_only(app, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    page = ArchiveClassificationPane(service)
    page.refresh(); settle(app, page)
    before = service.archive_classification.policy()
    page.comparison.setCurrentIndex(page.comparison.findData('at_least'))
    page.minimum_files.setValue(9)
    page.set_busy(True)
    assert not page.comparison.isEnabled()
    page.refresh(); page.set_busy(False); settle(app, page)
    assert page.comparison.currentData() == 'at_least' and page.minimum_files.value() == 9
    assert not page.preview_button.isEnabled()
    page.shutdown(); page.close()
    assert service.archive_classification.policy() == before


def test_comparison_and_number_fit_narrow_settings(app, tmp_path):
    service = DesktopService(tmp_path / 'data/workbox.db')
    page = ArchiveClassificationPane(service)
    try:
        page.resize(540, 560); page.show(); settle(app, page)
        for control in (page.comparison, page.minimum_files, page.high_type, page.low_type):
            assert control.isVisible() and control.width() >= 90
            assert control.mapTo(page, control.rect().topRight()).x() < page.width()
        assert page.comparison.width() >= page.comparison.fontMetrics().horizontalAdvance('恰好等于') + 30
    finally:
        page.shutdown(); page.close()
