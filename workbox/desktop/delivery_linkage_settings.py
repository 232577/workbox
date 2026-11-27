"""Configure optional delivery destinations and retry unfinished copies."""

import sqlite3

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget,
)

from workbox.desktop.fluent_views import WorkbenchListWidget
from workbox.desktop.ui import label
from workbox.desktop.ui_components import LineEdit, StatusBanner, button


_DESTINATIONS = {'server': '服务器', 'wechat': '微信待发送'}
_STATUSES = {'pending': '待处理', 'failed': '复制失败', 'cancelled': '已取消'}


class DeliveryLinkageSettings(QWidget):
    save_requested = Signal(str, str)
    retry_requested = Signal(int, str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._request = ''
        self._available = False
        self._directory_controls = []
        self.setAccessibleName('交付联动')

        page = QVBoxLayout(self)
        page.setContentsMargins(16, 12, 16, 10)
        page.setSpacing(8)
        page.addWidget(label('交付联动', 'pageTitle'))
        page.addWidget(label('交付后复制文件到已设置的目录。留空可停用对应目标。', 'subtle', True))

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        self.server_edit = self._directory_row(form, 0, '服务器目录', '本地目录或共享目录的完整路径')
        self.wechat_edit = self._directory_row(form, 1, '微信待发送目录', '本机目录的完整路径')
        page.addLayout(form)
        wechat_help = label('微信待发送目录中的文件需要你在微信中手动发送。', 'subtle', True)
        page.addWidget(wechat_help)

        self.save_button = button('保存设置', True)
        self.save_button.clicked.connect(self._save)
        self.result = StatusBanner()
        self.result.setAccessibleName('交付联动结果')
        self.result.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.result.hide()
        save_row = QHBoxLayout()
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.result, 1)
        page.addLayout(save_row)

        pending_header = QHBoxLayout()
        pending_header.setContentsMargins(0, 4, 0, 0)
        pending_header.addWidget(label('待处理记录', 'sectionTitle'))
        pending_header.addStretch()
        self.retry_button = button('重试')
        self.retry_button.setAccessibleName('重试选中的交付联动')
        self.retry_button.clicked.connect(self._retry)
        pending_header.addWidget(self.retry_button)
        page.addLayout(pending_header)

        self.pending_list = WorkbenchListWidget()
        self.pending_list.setAccessibleName('待处理的交付联动记录')
        self.pending_list.setMinimumHeight(72)
        self.pending_list.currentItemChanged.connect(self._sync_controls)
        page.addWidget(self.pending_list, 1)
        self.empty = label('没有待处理的交付联动。', 'subtle')
        page.addWidget(self.empty)
        self.pending_detail = label('', 'subtle', True)
        self.pending_detail.setAccessibleName('联动记录详情')
        self.pending_detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.pending_detail.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        page.addWidget(self.pending_detail)
        self._sync_controls()

    def _directory_row(self, form, row, name, placeholder):
        field = LineEdit()
        field.setAccessibleName(name)
        field.setPlaceholderText(placeholder)
        caption = label(name)
        caption.setBuddy(field)
        browse = button('选择目录')
        browse.setAccessibleName('选择' + name)
        browse.clicked.connect(lambda: self._browse(field))
        form.addWidget(caption, row, 0)
        form.addWidget(field, row, 1)
        form.addWidget(browse, row, 2)
        self._directory_controls.extend((field, browse))
        return field

    def _browse(self, field):
        path = QFileDialog.getExistingDirectory(self, '选择本地目录')
        if path:
            field.setText(path)

    def load(self):
        if not self._request:
            self._reload(load_settings=not self._available)

    def _reload(self, *, load_settings):
        linkage = getattr(self.service, 'delivery_linkage', None)
        if linkage is None:
            self._feedback('当前无法读取交付联动设置。', 'error')
            return
        try:
            settings = linkage.settings() if load_settings else None
            jobs = linkage.pending()
        except (OSError, sqlite3.Error, ValueError) as error:
            self._feedback(f'读取交付联动失败。{error}', 'error')
            return
        self._available = True
        if settings is not None:
            self.server_edit.setText(settings['server_root'])
            self.wechat_edit.setText(settings['wechat_root'])
        selected = self.pending_list.currentItem()
        selected_key = selected.data(Qt.UserRole) if selected else None
        with QSignalBlocker(self.pending_list):
            self.pending_list.clear()
            for job in jobs:
                key = (job['event_id'], job['target'])
                target = _DESTINATIONS[job['target']]
                status = _STATUSES[job['status']]
                item = QListWidgetItem(f'交付 #{key[0]}   {target} · {status}')
                detail = '\n'.join(filter(None, (job['message'], job['path'])))
                item.setData(Qt.UserRole, key)
                item.setToolTip(detail)
                self.pending_list.addItem(item)
                if key == selected_key:
                    self.pending_list.setCurrentItem(item)
            if jobs and self.pending_list.currentItem() is None:
                self.pending_list.setCurrentRow(0)
        self.pending_list.setVisible(bool(jobs))
        self.empty.setVisible(not jobs)
        self._sync_controls()

    def _sync_controls(self, *_):
        ready = self._available and not self._request
        for control in self._directory_controls:
            control.setEnabled(ready)
        self.save_button.setEnabled(ready)
        item = self.pending_list.currentItem()
        self.retry_button.setEnabled(ready and item is not None)
        detail = item.toolTip() if item else ''
        self.pending_detail.setText(detail)
        self.pending_detail.setVisible(bool(detail))

    def _save(self):
        self._request = 'save'
        self._sync_controls()
        self._feedback('正在保存设置…', 'busy')
        self.save_requested.emit(self.server_edit.text(), self.wechat_edit.text())

    def _retry(self):
        item = self.pending_list.currentItem()
        if item is None:
            return
        self._request = 'retry'
        self._sync_controls()
        self._feedback('正在重试复制…', 'busy')
        self.retry_requested.emit(*item.data(Qt.UserRole))

    def _feedback(self, text, tone):
        self.result.setText(text)
        self.result.setTone(tone)
        self.result.setVisible(bool(text))

    def show_result(self, text, *, reload=False):
        request, self._request = self._request, ''
        self._feedback(text, 'neutral')
        if reload:
            self._reload(load_settings=request == 'save')
        self._sync_controls()
