"""WorkBox's native, local-only desktop interface."""

from __future__ import annotations

import sqlite3
import threading
from collections import deque
from concurrent.futures import CancelledError
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from theme import apply_to, stylesheet, tokens, theme_signal
from dialog_lifecycle import transient_dialog
from app import WorkBox
from branding import brand_icon
from database import STATUSES
from parser import ParsedProject, parse_message

STYLE = stylesheet('light')


def label(text: str = "", role: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setObjectName(role)
    return widget


def button(text: str, role: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if role:
        widget.setObjectName(role)
    return widget


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(46)
    widget.setShowGrid(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    widget.setTextElideMode(Qt.ElideRight)
    return widget


def fill_table(widget: QTableWidget, rows: list[dict], values) -> None:
    widget.setRowCount(len(rows))
    for row_index, record in enumerate(rows):
        for column, value in enumerate(values(record)):
            item = QTableWidgetItem(str(value or "—"))
            item.setData(Qt.UserRole, record["id"])
            item.setToolTip(str(value or ""))
            widget.setItem(row_index, column, item)


def selected_id(widget: QTableWidget) -> int | None:
    item = widget.item(widget.currentRow(), 0)
    return item.data(Qt.UserRole) if item else None


def human_size(size: int) -> str:
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or suffix == "TB":
            return f"{size:,.1f} {suffix}" if suffix != "B" else f"{size:,} B"
        size /= 1024
    return ""


def app_icon() -> QIcon:
    return brand_icon()


class CaptureDialog(QDialog):
    def __init__(self, service: WorkBox, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("收录项目消息 · WorkBox")
        apply_to(self)
        self.resize(820, 740)
        self.setMinimumSize(680, 610)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(label("收录一条消息", "pageTitle"))
        layout.addWidget(label("粘贴你选择的项目消息。先检查拆分结果，再放进工作箱。", "muted"))
        self.batch_edit = QComboBox()
        self.batch_edit.setEditable(True)
        self.batch_edit.addItem(datetime.now().strftime("%Y-%m-%d 收件 %H:%M"))
        self.batch_edit.addItems([record["name"] for record in service.list_batches()])
        self.batch_edit.lineEdit().setMaxLength(100)
        layout.addWidget(label("收录到批次"))
        layout.addWidget(self.batch_edit)
        self.message_edit = QPlainTextEdit()
        self.message_edit.setPlaceholderText("在这里粘贴消息，例如：\n1. 客户档案管理系统\n2. 合同归档工具（加急）\n\n原文会完整保存，不读取微信，也不监听剪贴板。")
        self.message_edit.setMinimumHeight(145)
        self.message_edit.setAcceptDrops(False)
        layout.addWidget(self.message_edit, 2)
        preview_row = QHBoxLayout()
        self.preview_button = button("拆分预览")
        preview_row.addWidget(self.preview_button)
        preview_row.addStretch()
        self.add_button = button("＋ 添加一行")
        self.remove_button = button("移除选中行")
        preview_row.addWidget(self.add_button)
        preview_row.addWidget(self.remove_button)
        layout.addLayout(preview_row)
        self.preview_table = table(["项目名称 · 可直接修改", "加急"])
        self.preview_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        layout.addWidget(self.preview_table, 3)
        self.error_label = label("", "error", True)
        layout.addWidget(self.error_label)
        bottom = QHBoxLayout()
        bottom.addWidget(label("加急独立于开发状态；新项目默认为待开发。", "muted"))
        bottom.addStretch()
        cancel = button("取消")
        self.save_button = button("确认收录", "primary")
        self.save_button.setEnabled(False)
        bottom.addWidget(cancel)
        bottom.addWidget(self.save_button)
        layout.addLayout(bottom)
        self.preview_button.clicked.connect(self.preview)
        self.save_button.clicked.connect(self.save)
        self.add_button.clicked.connect(lambda: self.add_row(ParsedProject("")))
        self.remove_button.clicked.connect(self.remove_row)
        self.message_edit.textChanged.connect(self.invalidate_preview)
        cancel.clicked.connect(self.reject)

    def invalidate_preview(self):
        self.save_button.setEnabled(False)
        had_preview = self.preview_table.rowCount() > 0
        self.preview_table.setRowCount(0)
        self.error_label.setText("消息已修改，旧预览已清除。请重新拆分或手工添加项目。" if had_preview else "")

    def add_row(self, project: ParsedProject):
        row = self.preview_table.rowCount()
        self.preview_table.insertRow(row)
        self.preview_table.setItem(row, 0, QTableWidgetItem(project.name))
        urgent = QTableWidgetItem("")
        urgent.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        urgent.setCheckState(Qt.Checked if project.urgent else Qt.Unchecked)
        self.preview_table.setItem(row, 1, urgent)
        self.save_button.setEnabled(bool(self.message_edit.toPlainText().strip()))

    def remove_row(self):
        row = self.preview_table.currentRow()
        if row >= 0:
            self.preview_table.removeRow(row)
        self.save_button.setEnabled(self.preview_table.rowCount() > 0 and bool(self.message_edit.toPlainText().strip()))

    def preview(self):
        self.preview_table.setRowCount(0)
        self.save_button.setEnabled(False)
        try:
            projects = parse_message(self.message_edit.toPlainText())
            if not projects:
                raise ValueError("没有识别到项目。请填写消息，或添加一行手工录入项目名。")
            for project in projects:
                self.add_row(project)
            self.error_label.setText("")
        except ValueError as exc:
            self.error_label.setText(str(exc))

    def save(self):
        try:
            projects = [ParsedProject(
                self.preview_table.item(row, 0).text(),
                self.preview_table.item(row, 1).checkState() == Qt.Checked,
            ) for row in range(self.preview_table.rowCount())]
            self.service.capture_message(self.message_edit.toPlainText(), self.batch_edit.currentText(), projects)
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.error_label.setText(str(exc))
            return
        self.accept()


class FileJob(QThread):
    progress_changed = Signal(int)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""
        self.cancelled = False

    def run(self):
        last = -1

        def progress(done, total):
            nonlocal last
            percent = min(100, int(done * 100 / max(1, total)))
            if percent != last:
                self.progress_changed.emit(percent)
                last = percent

        try:
            self.result = self.operation(cancel_event=self.cancel_event, progress=progress)
        except CancelledError:
            self.cancelled = True
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class MainWindow(QMainWindow):
    def __init__(self, service: WorkBox):
        super().__init__()
        self.service = service
        self.worker: FileJob | None = None
        self.pending_paths = deque()
        self.job_kind = ""
        self.job_label = ""
        self.import_totals = {"added": 0, "duplicates": 0, "unmatched": 0}
        self.import_errors: list[str] = []
        self.closing = False
        self.current_project_id = None
        self._refreshing = False
        self.setWindowTitle("WorkBox 工作箱 v1.0")
        self.setWindowIcon(app_icon())
        apply_to(self)
        self.resize(1320, 860)
        self.setMinimumSize(1120, 720)
        self.setAcceptDrops(True)
        self._build()
        self.refresh()
        theme_signal().connect(self._update_status_colors)

    def _update_status_colors(self, _mode):
        colors = tokens()
        names = {'待开发': 'state_new', '开发中': 'state_working',
                 '待交付': 'state_pending', '已交付': 'state_delivered'}
        for row in range(self.project_table.rowCount()):
            item = self.project_table.item(row, 2)
            if item and item.text() in names:
                item.setForeground(QColor(colors[names[item.text()]]))

    @property
    def busy(self) -> bool:
        return self.worker is not None or bool(self.pending_paths)

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 30, 20, 24)
        side.setSpacing(12)
        mark = label("W", "brandMark")
        mark.setFixedSize(50, 50)
        mark.setAlignment(Qt.AlignCenter)
        side.addWidget(mark)
        side.addWidget(label("WorkBox", "brand"))
        side.addWidget(label("工作箱   /   v1.0", "muted"))
        side.addSpacing(24)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = []
        for index, text in enumerate(("项目收件箱", "批次", "ZIP 文件")):
            nav = button(text, "nav")
            nav.setCheckable(True)
            self.nav_group.addButton(nav, index)
            self.nav_buttons.append(nav)
            side.addWidget(nav)
        self.nav_buttons[0].setChecked(True)
        self.nav_group.idClicked.connect(self.show_page)
        side.addStretch()
        side.addWidget(label("从消息到交付", "sectionTitle"))
        side.addWidget(label("01   收录并确认项目\n\n02   追踪开发状态\n\n03   登记成品，确认交付", "muted", True))
        side.addSpacing(24)
        side.addWidget(label("只在本机保存\n不读取微信 · 不自动同步", "muted", True))
        outer.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(28, 28, 28, 18)
        main.setSpacing(15)
        outer.addLayout(main, 1)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        self.page_title = label("项目收件箱", "pageTitle")
        titles.addWidget(self.page_title)
        titles.addWidget(label("把项目留在这里，交付由你确认。", "muted"))
        top.addLayout(titles)
        top.addStretch()
        self.import_button = button("登记 ZIP")
        self.capture_button = button("＋ 收录消息", "primary")
        self.capture_button.clicked.connect(self.capture)
        self.import_button.clicked.connect(self.choose_zips)
        top.addWidget(self.import_button)
        top.addWidget(self.capture_button)
        main.addLayout(top)
        counters = QHBoxLayout()
        self.count_labels = {}
        for key, caption in (("projects", "项目"), ("batches", "批次"), ("zips", "ZIP")):
            self.count_labels[key] = label("0", "count")
            counters.addWidget(self.count_labels[key])
            counters.addWidget(label(caption, "muted"))
            counters.addSpacing(25)
        counters.addStretch()
        counters.addWidget(label("ZIP 保留原位，仅登记文件信息", "muted"))
        main.addLayout(counters)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索项目名、批次或 ZIP 名称")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumHeight(42)
        self.search_edit.textChanged.connect(self.refresh)
        main.addWidget(self.search_edit)
        self.notice_label = label("", "notice", True)
        self.notice_label.hide()
        main.addWidget(self.notice_label)
        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        self._build_projects()
        self._build_batches()
        self._build_zips()
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.cancel_button = button("取消读取")
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel_job)
        footer.addWidget(self.progress, 1)
        footer.addWidget(self.cancel_button)
        main.addLayout(footer)
        self.statusBar().showMessage(f"本地数据：{self.service.db.path}")

    def _build_projects(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        filters = QHBoxLayout()
        self.status_group = QButtonGroup(self)
        self.status_buttons = {}
        for index, status in enumerate((None, *STATUSES)):
            control = button(status or "全部", "filter")
            control.setCheckable(True)
            self.status_buttons[status] = control
            self.status_group.addButton(control, index)
            filters.addWidget(control)
        self.status_buttons[None].setChecked(True)
        self.status_group.idClicked.connect(self.refresh)
        filters.addStretch()
        self.urgent_filter = QCheckBox("只看加急")
        self.urgent_filter.toggled.connect(self.refresh)
        filters.addWidget(self.urgent_filter)
        self.batch_filter = QComboBox()
        self.batch_filter.setMaximumWidth(180)
        self.batch_filter.addItem("所有批次", None)
        self.batch_filter.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.batch_filter)
        layout.addLayout(filters)
        split = QSplitter()
        self.project_stack = QStackedWidget()
        self.project_table = table(["项目", "批次", "状态", "ZIP"])
        self.project_table.itemSelectionChanged.connect(self.select_project)
        self.project_stack.addWidget(self.project_table)
        empty = QFrame()
        empty.setObjectName("card")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(42, 50, 42, 50)
        empty_layout.addStretch()
        empty_layout.addWidget(label("INBOX / 01", "muted"))
        self.empty_label = label("工作箱准备好了", "emptyTitle", True)
        empty_layout.addWidget(self.empty_label)
        self.empty_hint = label("收录第一条项目消息。\n自动拆分后，你可以逐项检查和修改。", "muted", True)
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addSpacing(18)
        empty_capture = button("＋ 收录项目消息", "primary")
        empty_capture.clicked.connect(self.capture)
        empty_layout.addWidget(empty_capture, 0, Qt.AlignLeft)
        empty_layout.addStretch()
        empty_layout.addWidget(label("成品 ZIP 可以直接拖进窗口。\n原文件不会被移动、解压或运行。", "muted", True))
        self.project_stack.addWidget(empty)
        split.addWidget(self.project_stack)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setMinimumWidth(285)
        detail_scroll.setMaximumWidth(390)
        detail = QFrame()
        detail.setObjectName("card")
        content = QVBoxLayout(detail)
        content.setContentsMargins(18, 18, 18, 18)
        content.setSpacing(9)
        content.addWidget(label("项目详情", "muted"))
        self.detail_title = label("选中一个项目", "sectionTitle", True)
        self.detail_code = label("在这里管理状态与成品交付。", "muted", True)
        content.addWidget(self.detail_title)
        content.addWidget(self.detail_code)
        self.status_edit = QComboBox()
        self.status_edit.addItems(STATUSES)
        self.status_edit.model().item(3).setEnabled(False)
        self.status_edit.currentTextChanged.connect(self.change_status)
        self.status_edit.setAccessibleName("开发状态")
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_edit, 1)
        self.urgent_edit = QCheckBox("加急处理")
        self.urgent_edit.clicked.connect(self.change_urgent)
        status_row.addWidget(self.urgent_edit)
        content.addLayout(status_row)
        content.addWidget(label("成品 ZIP", "muted"))
        self.project_zip_edit = QComboBox()
        self.project_zip_edit.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.project_zip_edit.setMinimumContentsLength(12)
        self.project_zip_edit.currentIndexChanged.connect(self.update_zip_detail)
        content.addWidget(self.project_zip_edit)
        self.zip_metadata = QPlainTextEdit()
        self.zip_metadata.setReadOnly(True)
        self.zip_metadata.setFixedHeight(96)
        self.zip_metadata.setPlaceholderText("拖入成品 ZIP 后，将显示路径、大小和 SHA256。")
        content.addWidget(self.zip_metadata)
        self.confirm_button = button("确认已交付", "primary")
        self.confirm_button.clicked.connect(self.confirm_delivery_clicked)
        content.addWidget(self.confirm_button)
        self.delivery_label = label("仅登记文件不会自动完成交付。", "muted", True)
        content.addWidget(self.delivery_label)
        self.raw_toggle = button("查看收录原文")
        self.raw_toggle.setCheckable(True)
        content.addWidget(self.raw_toggle)
        self.raw_message = QPlainTextEdit()
        self.raw_message.setReadOnly(True)
        self.raw_message.setFixedHeight(120)
        self.raw_message.hide()
        self.raw_toggle.toggled.connect(self.raw_message.setVisible)
        self.raw_toggle.toggled.connect(lambda checked: self.raw_toggle.setText("收起收录原文" if checked else "查看收录原文"))
        content.addWidget(self.raw_message)
        content.addStretch()
        detail_scroll.setWidget(detail)
        split.addWidget(detail_scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([650, 325])
        layout.addWidget(split, 1)
        self.pages.addWidget(page)

    def _build_batches(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("同一批消息，放在一起。双击批次查看其中的项目。", "muted"))
        self.batch_table = table(["批次名称", "项目数", "已交付", "创建时间"])
        self.batch_table.itemDoubleClicked.connect(self.open_batch)
        layout.addWidget(self.batch_table, 1)
        self.pages.addWidget(page)

    def _build_zips(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        self.unmatched_filter = QCheckBox("只看待关联")
        self.unmatched_filter.toggled.connect(self.refresh)
        actions.addWidget(self.unmatched_filter)
        actions.addStretch()
        self.link_button = button("关联到项目…")
        self.link_button.clicked.connect(self.choose_zip_project)
        actions.addWidget(self.link_button)
        layout.addLayout(actions)
        self.zip_table = table(["ZIP 文件", "关联项目", "批次", "大小"])
        self.zip_table.itemSelectionChanged.connect(self.select_zip)
        self.zip_table.itemDoubleClicked.connect(self.choose_zip_project)
        layout.addWidget(self.zip_table, 1)
        self.library_metadata = QPlainTextEdit()
        self.library_metadata.setReadOnly(True)
        self.library_metadata.setMaximumHeight(130)
        self.library_metadata.setPlaceholderText("选择一个 ZIP 查看原始路径、大小和 SHA256。未匹配的 ZIP 可手工关联。")
        layout.addWidget(self.library_metadata)
        layout.addWidget(label("仅登记文件信息，不解压、不运行、不移动、不删除。已确认交付的 ZIP 保留原关联。", "muted", True))
        self.pages.addWidget(page)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(("项目收件箱", "批次", "ZIP 文件")[index])
        self.nav_buttons[index].setChecked(True)
        self.refresh()

    def notify(self, text: str):
        self.notice_label.setText(text)
        self.notice_label.setVisible(bool(text))
        self.notice_label.setToolTip(text)

    def refresh(self, *_):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            chosen = self.current_project_id
            query = self.search_edit.text()
            counts = self.service.db.counts()
            for key, value in counts.items():
                self.count_labels[key].setText(str(value))
            batches = self.service.list_batches()
            batch_id = self.batch_filter.currentData()
            self.batch_filter.blockSignals(True)
            self.batch_filter.clear()
            self.batch_filter.addItem("所有批次", None)
            for record in batches:
                self.batch_filter.addItem(record["name"], record["id"])
            self.batch_filter.setCurrentIndex(max(0, self.batch_filter.findData(batch_id)))
            self.batch_filter.blockSignals(False)
            status = next((key for key, control in self.status_buttons.items() if control.isChecked()), None)
            projects = self.service.list_projects(query, status, self.batch_filter.currentData(), self.urgent_filter.isChecked())
            self.project_table.blockSignals(True)
            fill_table(self.project_table, projects, lambda p: (
                ("加急 · " if p["urgent"] else "") + p["name"], p["batch_name"], p["status"],
                str(p["zip_count"]) if p["zip_count"] else "—",
            ))
            self.project_table.clearSelection()
            self.project_table.setCurrentCell(-1, -1)
            for index, project in enumerate(projects):
                self.project_table.item(index, 2).setForeground(QColor(tokens()[{
                    "待开发": "state_new", "开发中": "state_working", "待交付": "state_pending", "已交付": "state_delivered",
                }[project["status"]]]))
                if project["id"] == chosen:
                    self.project_table.selectRow(index)
            self.project_table.blockSignals(False)
            self.project_stack.setCurrentIndex(0 if projects else 1)
            self.empty_label.setText("没有匹配的项目" if counts["projects"] else "工作箱准备好了")
            self.empty_hint.setText("试试其他关键词，或切换状态与批次筛选。" if counts["projects"] else "收录第一条项目消息。\n自动拆分后，你可以逐项检查和修改。")
            fill_table(self.batch_table, [b for b in batches if query.casefold().strip() in b["name"].casefold()],
                       lambda b: (b["name"], str(b["project_count"]), str(b["delivered_count"]), b["created_at"][:10]))
            zip_id = selected_id(self.zip_table)
            self.zip_table.blockSignals(True)
            zips = self.service.list_zips(query, self.unmatched_filter.isChecked())
            fill_table(self.zip_table, zips, lambda z: (z["filename"], z["project_name"] or "待关联", z["batch_name"], human_size(z["size"])))
            for index, record in enumerate(zips):
                if record["id"] == zip_id:
                    self.zip_table.selectRow(index)
            self.zip_table.blockSignals(False)
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.notify(f"刷新失败：{exc}")
        finally:
            self._refreshing = False
        self.select_project()
        self.select_zip()
        self._update_actions()

    def _update_actions(self):
        self.capture_button.setEnabled(not self.busy)
        self.import_button.setEnabled(not self.busy)
        has_project = self.current_project_id is not None
        self.status_edit.setEnabled(has_project and not self.busy)
        self.urgent_edit.setEnabled(has_project and not self.busy)
        self.raw_toggle.setEnabled(has_project)
        self.project_zip_edit.setEnabled(has_project and self.project_zip_edit.count() > 0 and not self.busy)
        self.link_button.setEnabled(selected_id(self.zip_table) is not None and not self.busy)
        self.confirm_button.setEnabled(has_project and not self.busy
            and self.status_edit.currentText() == "待交付" and self.project_zip_edit.currentData() is not None)

    def select_project(self):
        if self._refreshing:
            return
        project_id = selected_id(self.project_table)
        self.current_project_id = project_id
        self.status_edit.blockSignals(True)
        self.project_zip_edit.blockSignals(True)
        self.project_zip_edit.clear()
        if project_id is None:
            self.detail_title.setText("选中一个项目")
            self.detail_code.setText("在这里管理状态与成品交付。")
            self.raw_message.clear()
            self.urgent_edit.setChecked(False)
            self.status_edit.setCurrentIndex(0)
            self.delivery_label.setText("仅登记文件不会自动完成交付。")
        else:
            project = self.service.get_project(project_id)
            self.detail_title.setText(project["name"])
            self.detail_code.setText(f"WB-{project_id:06d}  /  {project['batch_name']}")
            self.status_edit.setCurrentText(project["status"])
            self.urgent_edit.setChecked(bool(project["urgent"]))
            self.raw_message.setPlainText(project["raw_text"])
            for record in project["zips"]:
                self.project_zip_edit.addItem(record["filename"], record["id"])
            if project["delivered_zip_id"]:
                self.project_zip_edit.setCurrentIndex(self.project_zip_edit.findData(project["delivered_zip_id"]))
            self.delivery_label.setText(
                f"已人工确认 · {project['delivered_at'].replace('T', ' ')[:19]} UTC"
                if project["delivered_at"] else "仅登记文件不会自动完成交付。"
            )
        self.project_zip_edit.blockSignals(False)
        self.status_edit.blockSignals(False)
        self.update_zip_detail()

    def metadata_text(self, record):
        exists = Path(record["path"]).is_file()
        return (f"{record['path']}\n\n大小：{record['size']:,} 字节 ({human_size(record['size'])})"
                f"\nSHA256：\n{record['sha256']}\n\n"
                + ("原路径存在 · 确认交付时重新校验内容" if exists else "原文件未找到 · 不能确认交付，请重新拖入文件"))

    def update_zip_detail(self, *_):
        zip_id = self.project_zip_edit.currentData()
        self.zip_metadata.setPlainText(self.metadata_text(self.service.db.get_zip(zip_id)) if zip_id else "")
        self._update_actions()

    def select_zip(self):
        zip_id = selected_id(self.zip_table)
        self.library_metadata.setPlainText(self.metadata_text(self.service.db.get_zip(zip_id)) if zip_id else "")
        self._update_actions()

    def capture(self):
        if self.busy:
            return
        with transient_dialog(CaptureDialog(self.service, self)) as dialog:
            if dialog.exec() == QDialog.Accepted:
                self.notify("消息已收录，原文和项目已保存在本机。")
                self.refresh()

    def change_status(self, status):
        if self.current_project_id is None or self._refreshing or self.busy:
            return
        try:
            self.service.set_status(self.current_project_id, status)
        except (ValueError, KeyError, sqlite3.Error) as exc:
            self.notify(str(exc))
        self.refresh()

    def change_urgent(self, urgent):
        if self.current_project_id is not None and not self.busy:
            try:
                self.service.set_urgent(self.current_project_id, urgent)
            except (ValueError, KeyError, sqlite3.Error) as exc:
                self.notify(str(exc))
            self.refresh()

    def open_batch(self, *_):
        batch_id = selected_id(self.batch_table)
        if batch_id is not None:
            self.search_edit.clear()
            self.status_buttons[None].setChecked(True)
            self.batch_filter.setCurrentIndex(self.batch_filter.findData(batch_id))
            self.show_page(0)

    def choose_zips(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择完成的 ZIP（文件保持原位）", "", "ZIP 文件 (*.zip *.ZIP)")
        if paths:
            self.import_paths(paths)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and urls and all(url.isLocalFile() and Path(url.toLocalFile()).suffix.casefold() == ".zip" for url in urls):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and urls and all(url.isLocalFile() and Path(url.toLocalFile()).suffix.casefold() == ".zip" for url in urls):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.import_paths([url.toLocalFile() for url in urls])
        else:
            event.ignore()

    def import_paths(self, paths):
        if self.busy:
            self.notify("正在读取文件，请等待完成或取消后再拖入。")
            return
        self.pending_paths.extend(dict.fromkeys(str(path) for path in paths))
        self.import_totals = {"added": 0, "duplicates": 0, "unmatched": 0}
        self.import_errors = []
        self._next_import()

    def _next_import(self):
        if self.pending_paths:
            path = self.pending_paths.popleft()
            self._start_job("import", Path(path).name, partial(self.service.import_zip, path))

    def _start_job(self, kind, title, operation):
        self.job_kind, self.job_label = kind, title
        self.worker = FileJob(operation, self)
        self.worker.progress_changed.connect(self.progress.setValue)
        self.worker.finished.connect(self._job_finished)
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_button.show()
        self.notify(f"{'读取并登记' if kind == 'import' else '重新校验交付文件'}：{title}")
        self._update_actions()
        self.worker.start()

    @Slot()
    def _job_finished(self):
        worker = self.worker
        if worker is None:
            return
        kind = self.job_kind
        if worker.error:
            self.import_errors.append(f"{self.job_label}：{worker.error}")
            self.notify(f"操作失败：{worker.error}")
        elif worker.cancelled:
            self.notify("已取消当前读取。此前成功登记的文件信息仍保留，原文件未改变。")
        elif kind == "import":
            result = worker.result
            self.import_totals["duplicates" if result["duplicate"] else "added"] += 1
            self.import_totals["unmatched"] += int(result["zip"]["project_id"] is None)
        else:
            self.notify("已确认交付，并记录了本次 ZIP 和确认时间。")
        self.worker = None
        worker.deleteLater()
        self.refresh()
        if self.closing:
            QTimer.singleShot(0, self.close)
            return
        if self.pending_paths:
            self._next_import()
            return
        self.progress.hide()
        self.cancel_button.hide()
        self._update_actions()
        if kind == "import" and not worker.cancelled:
            totals = self.import_totals
            text = f"ZIP 登记完成：新增 {totals['added']} · 已有 {totals['duplicates']} · 待关联 {totals['unmatched']} · 失败 {len(self.import_errors)}。"
            if self.import_errors:
                text += "\n" + "\n".join(self.import_errors[:3])
            elif totals["unmatched"]:
                text += " 请到「ZIP 文件」选择对应项目。"
            self.notify(text)

    def cancel_job(self):
        self.pending_paths.clear()
        if self.worker is not None:
            self.worker.cancel_event.set()

    def confirm_delivery_clicked(self):
        project_id = self.current_project_id
        zip_id = self.project_zip_edit.currentData()
        if self.busy or project_id is None or zip_id is None:
            return
        project = self.service.get_project(project_id)
        record = self.service.db.get_zip(zip_id)
        box = QMessageBox(self)
        box.setWindowTitle("确认交付 · WorkBox")
        box.setIcon(QMessageBox.Question)
        box.setTextFormat(Qt.PlainText)
        box.setText(f"确认「{project['name']}」已经交付？")
        box.setInformativeText(f"本次成品：{record['filename']}\n\n此操作只记录你已完成交付，不发送文件。\n确认后将重新读取原文件并核验 SHA256。")
        box.setDetailedText(self.metadata_text(record))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("是，确认已交付")
        box.button(QMessageBox.No).setText("尚未交付")
        box.setDefaultButton(QMessageBox.No)
        with transient_dialog(box):
            if box.exec() == QMessageBox.Yes:
                self._start_job("delivery", record["filename"], partial(
                    self.service.confirm_delivery, project_id, zip_id, confirmed=True
                ))

    def choose_zip_project(self, *_):
        zip_id = selected_id(self.zip_table)
        if self.busy or zip_id is None:
            return
        projects = self.service.list_projects()
        if not projects:
            self.notify("请先收录一个项目，再关联 ZIP。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("将 ZIP 关联到项目")
        dialog.resize(620, 220)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(label(self.service.db.get_zip(zip_id)["filename"], "sectionTitle", True))
        choice = QComboBox()
        choice.addItem("请选择项目，注意核对批次", None)
        for project in projects:
            choice.addItem(f"WB-{project['id']:06d}  |  {project['name']}  |  {project['batch_name']}", project["id"])
        layout.addWidget(choice)
        confirm = button("确认关联", "primary")
        confirm.setEnabled(False)
        choice.currentIndexChanged.connect(lambda: confirm.setEnabled(choice.currentData() is not None))
        confirm.clicked.connect(dialog.accept)
        layout.addWidget(confirm, 0, Qt.AlignRight)
        with transient_dialog(dialog):
            if dialog.exec() == QDialog.Accepted:
                try:
                    self.service.link_zip(zip_id, choice.currentData())
                    self.notify("ZIP 已关联，尚未确认交付。")
                except (ValueError, KeyError, sqlite3.Error) as exc:
                    self.notify(str(exc))
                self.refresh()

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.cancel_job()
            event.ignore()
            self.notify("正在安全停止文件读取，完成后关闭窗口。")
        else:
            event.accept()
