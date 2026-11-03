"""On-demand update dialog. Network work never blocks or owns business data."""
from pathlib import Path
import os
import sys

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QPlainTextEdit, QProgressBar, QMessageBox

from theme import apply_to
from ui import FileJob, button, label, human_size
from update_client import CURRENT_TAG, check_for_update, download_release, verify_installer
from ui_components import DialogHeader, StatusBanner
from qfluentwidgets import FluentIcon


class UpdateDialog(QDialog):
    def __init__(self, parent=None, *, cache_root=None):
        super().__init__(parent)
        self.worker = None
        self.release = self.installer = None
        self._job_kind = ''
        self.phase = 'idle'
        self._close_pending = self._closed = False
        self.cache_root = Path(cache_root) if cache_root is not None else Path(
            os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WorkBox' / 'updates'
        self.setWindowTitle('检查更新 · WorkBox')
        self.resize(680, 540)
        self.setMinimumSize(500, 460)
        apply_to(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        self.header=DialogHeader('软件更新', '当前构建：' + CURRENT_TAG,
                                 icon=FluentIcon.DOWNLOAD, eyebrow='WORKBOX / 更新中心')
        layout.addWidget(self.header)
        self.stage_label=label('1 检查版本  →  2 下载与校验  →  3 确认安装', 'updateStages', True)
        self.stage_label.setAccessibleName('更新阶段')
        layout.addWidget(self.stage_label)
        self.status_label = StatusBanner('点击检查更新后才连接官方项目发布源。')
        layout.addWidget(self.status_label)
        layout.addWidget(label('版本说明', 'sectionTitle'))
        self.notes = QPlainTextEdit()
        self.notes.setObjectName('releaseNotes')
        self.notes.setAccessibleName('版本说明')
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText('新版说明会显示在这里。不会上传项目、文档或数据库。')
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        layout.addWidget(label('来源：232577/workbox · HTTPS + SHA-256校验。安装包尚无商业代码签名。', 'muted', True))
        layout.addWidget(label('安装需确认；先安全退出并保存数据库恢复点。项目文件仍请自行备份。', 'muted', True))
        self.mode_note=label('当前为源码运行：可检查和下载，不会替换开发目录。','muted',True)
        layout.addWidget(self.mode_note)
        row = QHBoxLayout()
        self.check_button = button('检查更新')
        self.download_button = button('下载更新', 'primary')
        self.install_button = button('退出并安装', 'primary')
        self.close_button = button('关闭')
        for widget in (self.check_button, self.download_button, self.install_button, self.close_button):
            widget.setAccessibleName(widget.text())
            row.addWidget(widget)
        layout.addLayout(row)
        self.check_button.clicked.connect(self.check)
        self.download_button.clicked.connect(self.download)
        self.install_button.clicked.connect(self.confirm_install)
        self.close_button.clicked.connect(self.reject)
        self._sync()

    def _sync(self):
        idle = self.worker is None and not self._closed
        self.check_button.setEnabled(idle)
        self.download_button.setEnabled(idle and self.release is not None)
        supported = sys.platform == 'win32' and getattr(sys, 'frozen', False)
        self.install_button.setEnabled(idle and self.installer is not None and supported)
        self.install_button.setToolTip('源码运行只检查和下载，不会替换开发目录。' if not supported else '保存恢复点并安全退出后打开安装向导。')
        self.close_button.setText('取消' if self.worker is not None else '关闭')
        self.close_button.setAccessibleName(self.close_button.text())
        self.mode_note.setVisible(not supported)
        self.download_button.setVisible(self.release is not None and self.installer is None)
        self.install_button.setVisible(self.installer is not None)
        stages={
            'idle':'1 检查版本  →  2 下载与校验  →  3 确认安装',
            'check':'正在检查版本  →  下载与校验  →  确认安装',
            'available':'版本已确认  →  下一步：下载与校验  →  确认安装',
            'download':'版本已确认  →  正在下载与校验  →  确认安装',
            'verified':'版本已确认  →  校验已通过  →  下一步：确认安装',
            'current':'版本检查完成 · 没有更高的正式构建',
            'cancelled':'操作已取消 · 可重新检查',
            'error':'本次操作未完成 · 现有程序保持不变',
            'install':'校验已通过 · 正在交给安全退出与安装流程',
        }
        self.stage_label.setText(stages[self.phase])

    def _start(self, kind, operation):
        if self.worker is not None or self._closed:
            return
        self._job_kind = kind
        self.phase = kind
        self.status_label.setTone('busy')
        self.worker = FileJob(operation, self)
        self.worker.progress_changed.connect(self.progress.setValue)
        self.worker.finished.connect(self._finished)
        self.progress.setRange(0, 0 if kind == 'check' else 100)
        self.progress.setValue(0)
        self.progress.show()
        self._sync()
        self.worker.start()

    def check(self):
        if self.worker is not None:
            return
        self.release = self.installer = None
        self.notes.clear()
        self.status_label.setText('正在读取官方发行记录…')
        self._start('check', check_for_update)

    def download(self):
        if self.release is None or self.worker is not None:
            return
        self.installer = None
        release = self.release
        self.status_label.setText('正在下载并核对安装包，现有版本继续运行。')
        self._start('download', lambda **kw: download_release(release, self.cache_root, **kw))

    def _finished(self):
        worker = self.sender()
        if worker is None or worker is not self.worker:
            return
        self.worker = None
        if not self._closed:
            self.progress.hide()
            if worker.cancelled:
                self.phase='cancelled';self.status_label.setTone('neutral')
                self.status_label.setText('已取消；现有版本和数据未改变。')
            elif worker.error:
                self.phase='error';self.status_label.setTone('error')
                self.status_label.setText('更新操作未完成：' + worker.error[:500])
            elif self._job_kind == 'check':
                self.release = worker.result
                if self.release is None:
                    self.phase='current';self.status_label.setTone('success')
                    self.status_label.setText('没有更新：当前构建不低于最新正式发布。')
                else:
                    self.phase='available';self.status_label.setTone('neutral')
                    self.status_label.setText('发现新构建：' + self.release.tag + ' · ' + human_size(self.release.size))
                    self.notes.setPlainText(self.release.notes)
            else:
                self.phase='verified';self.status_label.setTone('success')
                self.installer = worker.result
                self.status_label.setText('下载完成，长度与SHA-256校验通过。可退出并安装。')
            self._sync()
        worker.deleteLater()
        if self._close_pending:
            super().reject()

    def confirm_install(self):
        if not self.install_button.isEnabled() or self.release is None or self.installer is None:
            return
        try:
            verify_installer(self.installer, self.release)
        except (OSError, ValueError) as error:
            self.installer = None
            self.phase='error';self.status_label.setTone('error')
            self.status_label.setText(str(error))
            self._sync()
            return
        answer = QMessageBox.question(self, '确认升级 WorkBox',
            '即将安全退出，创建数据库恢复点并打开安装向导。\n'
            '原项目文件和交付归档不会删除。请先保存正在编辑的其他资料。\n\n继续退出并安装？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.phase='install';self._sync()
            self.accept()

    def reject(self):
        if self.worker is not None:
            self._close_pending = True
            self.worker.cancel_event.set()
            self.status_label.setText('正在取消网络操作，完成后关闭；不会强制结束线程。')
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
