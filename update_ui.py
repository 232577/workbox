"""On-demand update dialog. Network work never blocks or owns business data."""
from pathlib import Path
import os
import sys

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QPlainTextEdit, QProgressBar

from theme import apply_to
from ui import FileJob, button, label, human_size
from update_client import CURRENT_TAG, check_for_update, download_release
from ui_components import DialogHeader, StatusBanner
from qfluentwidgets import FluentIcon


class _UpdateJob(FileJob):
    """下载器只报告文本，由主线程接收并更新界面。"""
    status_changed = Signal(str)

    def __init__(self, operation, parent=None, *, report_status=False):
        super().__init__(operation, parent)
        if report_status:
            self.operation = lambda **kwargs: operation(status=self.status_changed.emit, **kwargs)


class UpdateDialog(QDialog):
    def __init__(self, parent=None, *, cache_root=None):
        super().__init__(parent)
        self.worker = None
        self.release = self.installer = None
        self._job_kind = ''
        self._download_status = ''
        self._close_pending = self._closed = False
        self._can_install = sys.platform == 'win32' and getattr(sys, 'frozen', False)
        self.cache_root = Path(cache_root) if cache_root is not None else Path(
            os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WorkBox' / 'updates'
        self.setWindowTitle('检查更新 · WorkBox')
        self.setMinimumWidth(460)
        apply_to(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        self.header = DialogHeader('软件更新', '当前构建 ' + CURRENT_TAG,
                                   icon=FluentIcon.DOWNLOAD)
        layout.addWidget(self.header)
        self.status_label = StatusBanner('可以检查是否有新的正式版本。')
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.notes_heading = label('版本说明', 'sectionTitle')
        layout.addWidget(self.notes_heading)
        self.notes = QPlainTextEdit()
        self.notes.setObjectName('releaseNotes')
        self.notes.setAccessibleName('版本说明')
        self.notes.setReadOnly(True)
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('更新进度')
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.mode_note = label(
            '请先保存手头的工作。WorkBox 会备份数据库、退出并在更新完成后重新打开。'
            if self._can_install else '源码运行仅提供版本检查和安装包下载。', 'muted', True)
        layout.addWidget(self.mode_note)
        row = QHBoxLayout()
        row.addStretch()
        self.check_button = button('检查更新', 'primary')
        self.update_button = button('更新并重启' if self._can_install else '下载更新', 'primary')
        self.close_button = button('关闭')
        for widget in (self.check_button, self.update_button, self.close_button):
            widget.setAutoDefault(False)
            widget.setAccessibleName(widget.text())
            row.addWidget(widget)
        self.close_button.setDefault(True)
        layout.addLayout(row)
        self.check_button.clicked.connect(self.check)
        self.update_button.clicked.connect(self.download)
        self.close_button.clicked.connect(self.reject)
        self._sync()
        self.resize(560, self.sizeHint().height())

    def _sync(self):
        idle = self.worker is None and not self._closed
        available = self.release is not None
        self.check_button.setVisible(not available)
        self.check_button.setEnabled(idle)
        self.update_button.setVisible(available and self.installer is None)
        self.update_button.setEnabled(idle and available and self.installer is None)
        self.close_button.setText('取消' if self.worker is not None else '关闭')
        self.close_button.setAccessibleName(self.close_button.text())
        self.close_button.setEnabled(not self._close_pending)
        self.mode_note.setVisible(available or not self._can_install)
        self.notes_heading.setVisible(available)
        self.notes.setVisible(available)

    def _start(self, kind, operation, *, report_status=False):
        if self.worker is not None or self._closed:
            return
        self._job_kind = kind
        self.status_label.setTone('busy')
        self.worker = _UpdateJob(operation, self, report_status=report_status)
        self.worker.progress_changed.connect(self._show_progress)
        self.worker.status_changed.connect(self._show_download_status, Qt.QueuedConnection)
        self.worker.finished.connect(self._finished)
        self.progress.setRange(0, 0 if kind == 'check' else 100)
        self.progress.setValue(0)
        self.progress.show()
        self._sync()
        self.worker.start()

    def check(self):
        if self.worker is not None or self._closed:
            return
        self.release = self.installer = None
        self.notes.clear()
        self.status_label.setText('正在读取官方发行记录…')
        self._start('check', check_for_update)

    def download(self):
        if self.release is None or self.installer is not None or self.worker is not None or self._closed:
            return
        release = self.release
        self._download_status = ('正在下载并校验增量更新包…' if self._offered_delta() is not None
                                 else '正在下载并校验安装包…')
        self.status_label.setText(self._download_status)
        if self._can_install:
            from incremental_update import download_update
            app_dir = Path(sys.executable).resolve().parent
            self._start('download', lambda **kw: download_update(
                release, self.cache_root, app_dir=app_dir, **kw), report_status=True)
        else:
            self._start('download', lambda **kw: download_release(release, self.cache_root, **kw))

    def _offered_delta(self):
        delta = getattr(self.release, 'delta', None)
        if (not self._can_install or delta is None
                or delta.from_tag != CURRENT_TAG or delta.to_tag != self.release.tag
                or type(delta.size) is not int or not 0 < delta.size < self.release.size):
            return None
        return delta

    @Slot(str)
    def _show_download_status(self, text):
        if (self.sender() is not self.worker or self._close_pending or self._closed
                or self._job_kind != 'download'):
            return
        self._download_status = text
        self.status_label.setText(text)

    def _show_progress(self, percent):
        if self.sender() is not self.worker or self._close_pending or self._closed:
            return
        self.progress.setValue(percent)
        if self._job_kind == 'download':
            self.status_label.setText(f'{self._download_status} {percent}%')

    def _finished(self):
        worker = self.sender()
        if worker is None or worker is not self.worker:
            return
        self.worker = None
        self.progress.hide()
        worker.deleteLater()
        if self._close_pending:
            super().reject()
            return
        if self._closed:
            return
        if worker.cancelled:
            self.status_label.setTone('neutral')
            self.status_label.setText('已取消更新操作。')
        elif worker.error:
            self.status_label.setTone('error')
            self.status_label.setText('更新操作未完成。' + worker.error[:500])
        elif self._job_kind == 'check':
            self.release = worker.result
            if self.release is None:
                self.status_label.setTone('success')
                self.status_label.setText('没有更新。')
            else:
                self.status_label.setTone('neutral')
                delta = self._offered_delta()
                size_note = (f'增量下载 {human_size(delta.size)} · 完整安装包 '
                             f'{human_size(self.release.size)} 可作兜底' if delta is not None
                             else human_size(self.release.size))
                self.status_label.setText(f'可更新至 {self.release.tag} · {size_note}')
                self.notes.setPlainText(self.release.notes)
        else:
            self.installer = Path(worker.result)
            if self._can_install:
                self.accept()
                return
            self.status_label.setTone('success')
            self.status_label.setText(f'安装包已下载并校验。\n{self.installer}')
        self._sync()

    def reject(self):
        if self.worker is not None:
            self._close_pending = True
            self.worker.cancel_event.set()
            self.status_label.setText('正在取消，稍后关闭窗口…')
            self._sync()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            self.reject(); event.ignore()
        else:
            super().closeEvent(event)

    def shutdown(self):
        if self.worker is not None:
            self.worker.cancel_event.set()
            if not self.worker.wait(15000):
                raise RuntimeError('更新网络任务尚未安全结束，不能销毁窗口或安装。')
            self.worker.finished.disconnect(self._finished)
            self.worker.deleteLater()
            self.worker = None
        self._closed = True
