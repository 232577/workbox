"""On-demand update dialog. Network work never blocks or owns business data."""
from pathlib import Path
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QPlainTextEdit, QProgressBar

from theme import apply_to
from ui import FileJob, button, label, human_size
from update_client import CURRENT_TAG, check_for_update, download_release
from ui_components import DialogHeader, StatusBanner
from qfluentwidgets import FluentIcon


class UpdateDialog(QDialog):
    def __init__(self, parent=None, *, cache_root=None):
        super().__init__(parent)
        self.worker = None
        self.release = self.installer = None
        self._job_kind = ''
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

    def _start(self, kind, operation):
        if self.worker is not None or self._closed:
            return
        self._job_kind = kind
        self.status_label.setTone('busy')
        self.worker = FileJob(operation, self)
        self.worker.progress_changed.connect(self._show_progress)
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
        self.status_label.setText('正在下载并校验安装包…')
        self._start('download', lambda **kw: download_release(release, self.cache_root, **kw))

    def _show_progress(self, percent):
        if self.sender() is not self.worker or self._close_pending:
            return
        self.progress.setValue(percent)
        if self._job_kind == 'download':
            self.status_label.setText(f'正在下载并校验安装包… {percent}%')

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
                self.status_label.setText(f'可更新至 {self.release.tag} · {human_size(self.release.size)}')
                self.notes.setPlainText(self.release.notes)
        else:
            self.installer = worker.result
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
