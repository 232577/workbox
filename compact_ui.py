"""Compact project/ZIP management, opened only on demand from the floating ball."""

from pathlib import Path
from datetime import date, datetime
import sqlite3
import re

from PySide6.QtCore import Qt, Signal, QSize, QDate, QLocale, QPoint, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QPalette, QContextMenuEvent, QActionGroup
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QFileDialog, QSpinBox, QScrollArea,
    QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QTableWidget, QMenu, QListView,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QSizePolicy, QScrollBar, QStyleFactory, QProxyStyle, QStyle, QSplitter,
    QStyledItemDelegate, QStyleOptionViewItem,
)

from theme import apply_to, apply_scrollbar, stylesheet, install_theme, set_theme, current_mode, theme_signal
from dialog_lifecycle import transient_dialog
from ui_components import button as fluent_button, LineEdit as QLineEdit, CheckBox as QCheckBox, SpinBox as QSpinBox, ComboBox as QComboBox
from qfluentwidgets import FluentIcon, InfoBadge, InfoLevel
from database import STATUSES
from floating import DropSurface
from parser import ParsedProject
from ui import human_size, app_icon

STYLE = stylesheet('light')


def label(text='', name='', wrap=False):
    item = QLabel(text)
    item.setTextFormat(Qt.PlainText)
    item.setWordWrap(wrap)
    item.setObjectName(name)
    return item


def button(text, primary=False):
    return fluent_button(text, primary)


def local_timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, AttributeError):
        return str(value)


def selected(widget):
    item = widget.currentItem()
    return item.data(Qt.UserRole) if item else None


class StandardScrollMetrics(QProxyStyle):
    """只调整 Qt 标准滑块的最小尺寸；绘制、输入与拖动仍由 Qt 实现。"""
    def pixelMetric(self, metric, option=None, widget=None):
        value = super().pixelMetric(metric, option, widget)
        return max(32, value) if metric == QStyle.PM_ScrollBarSliderMin else value


def zip_delivery_hint(settings):
    if not settings.get('auto_deliver_zip', True):
        return 'ZIP 自动交付已关闭，新版本归档后为待交付；请在项目右键人工确认交付。'
    minimum = settings.get('zip_delivery_min_files', 4)
    return f'每项目至少 {minimum} 个文件自动设为已交付，少于 {minimum} 个文件设为待交付。'


class WrappedNameDelegate(QStyledItemDelegate):
    """只提供断行提示和可用宽度，测量与绘制均沿用 Qt 标准委托。"""
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # Qt 的 WordWrap 不拆无空格长英文；显示选项加入零宽断行机会，模型原文不变。
        option.text = re.sub(r'[A-Za-z0-9_./\\-]{12,}',
                             lambda match: '\u200b'.join(match.group()), option.text)
        option.features |= QStyleOptionViewItem.WrapText
        option.textElideMode = Qt.ElideNone

    def sizeHint(self, option, index):
        view = self.parent()
        option = QStyleOptionViewItem(option)
        if isinstance(view, QListWidget):
            option.rect.setWidth(view.viewport().width())
        elif isinstance(view, QTableWidget):
            option.rect.setWidth(view.columnWidth(index.column()))
        self.initStyleOption(option, index)
        # 样式表的单元格内边距必须先从可用宽度扣除，否则 Qt 会按更宽的行测量高度。
        text_rect = view.style().subElementRect(QStyle.SE_ItemViewItemText, option, view)
        option.rect.setWidth(max(1, text_rect.width()))
        size = super().sizeHint(option, index)
        if isinstance(view, QListWidget):
            size.setWidth(view.viewport().width())
        return size


class ProjectList(QListWidget):
    """用实际右击行或键盘焦点行定位菜单，空白区域不借用旧选择。"""
    project_context_requested = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)
        self.setResizeMode(QListView.Adjust)
        self.setItemDelegate(WrappedNameDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def contextMenuEvent(self, event):
        keyboard = event.reason() == QContextMenuEvent.Keyboard
        item = self.currentItem() if keyboard else self.itemAt(event.pos())
        if item is not None:
            point = self.visualItemRect(item).center() if keyboard else event.pos()
            self.project_context_requested.emit(item.data(Qt.UserRole), self.viewport().mapToGlobal(point))
        event.accept()

    def resizeEvent(self, event):
        # 宽度变化会改变换行高度；按当前正在阅读的顶行定位，不借用选中行。
        item = self.itemAt(QPoint(2, 0))
        anchor_id = item.data(Qt.UserRole) if item else None
        anchor_top = self.visualItemRect(item).top() if item else 0
        super().resizeEvent(event)
        self.doItemsLayout()
        if anchor_id is not None and not self.verticalScrollBar().isSliderDown():
            item = next((self.item(i) for i in range(self.count())
                         if self.item(i).data(Qt.UserRole) == anchor_id), None)
            if item is not None:
                self.scrollToItem(item, QAbstractItemView.PositionAtTop)
                offset = max(anchor_top, 1 - self.visualItemRect(item).height())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - offset)


def standard_list_scrolling(widget):
    """采用 Qt 官方滚动条和 Windows 标准样式，保留原生拖动、箭头及键盘行为。"""
    widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    widget.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    if hasattr(widget,'setUniformItemSizes'):
        widget.setUniformItemSizes(not isinstance(widget, ProjectList))
    style = StandardScrollMetrics(QStyleFactory.create('Windows') or QStyleFactory.create('Fusion'))
    style.setParent(widget)
    widget._scrollbar_style = style
    bar = QScrollBar(Qt.Vertical, widget)
    bar.setStyle(style)
    bar.setMinimumWidth(18)
    bar.setAccessibleName((widget.accessibleName() or '列表') + '滚动条')
    apply_scrollbar(bar)
    bar.setTracking(True)
    widget.setVerticalScrollBar(bar)
    widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)


def populate(widget, rows, caption, *, clear_selection=False):
    values = [(row['id'], caption(row)) for row in rows]
    previous = selected(widget)
    selected_ids = {item.data(Qt.UserRole) for item in widget.selectedItems()}
    bar = widget.verticalScrollBar()
    old_value = bar.value()
    anchor = widget.itemAt(QPoint(5, 5))
    anchor_id = anchor.data(Qt.UserRole) if anchor else None
    anchor_top = widget.visualItemRect(anchor).top() if anchor else 0
    if clear_selection:
        previous, selected_ids = None, set()
    same_order = [widget.item(i).data(Qt.UserRole) for i in range(widget.count())] == [pid for pid, _ in values]
    was_blocked = widget.blockSignals(True)
    try:
        if same_order:
            # 空轮询不重建模型，也不重新选择当前项，避免滚动回跳与拖动中断。
            changed = False
            for index, (_, text) in enumerate(values):
                item = widget.item(index)
                if item.text() != text:
                    item.setText(text)
                    item.setToolTip(text)
                    changed = True
            if changed and isinstance(widget, ProjectList):
                widget.doItemsLayout()
                anchor = next((widget.item(i) for i in range(widget.count())
                               if widget.item(i).data(Qt.UserRole) == anchor_id), None)
                if anchor is not None:
                    widget.scrollToItem(anchor, QAbstractItemView.PositionAtTop)
                    bar.setValue(bar.value() - max(anchor_top, 1 - widget.visualItemRect(anchor).height()))
            if clear_selection:
                widget.setCurrentItem(None)
                widget.clearSelection()
                bar.setValue(0)
            return
        widget.clear()
        for pid, text in values:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, pid)
            if not isinstance(widget, ProjectList):
                item.setSizeHint(QSize(0, 68))
            item.setToolTip(text)
            widget.addItem(item)
            if pid == previous:
                widget.setCurrentItem(item)
        for index in range(widget.count()):
            item = widget.item(index)
            item.setSelected(item.data(Qt.UserRole) in selected_ids)
        widget.doItemsLayout()
        if clear_selection:
            bar.setValue(0)
        else:
            anchor = next((widget.item(i) for i in range(widget.count()) if widget.item(i).data(Qt.UserRole) == anchor_id), None)
            if anchor is not None and widget.verticalScrollMode() == QAbstractItemView.ScrollPerPixel:
                widget.scrollToItem(anchor, QAbstractItemView.PositionAtTop)
                bar.setValue(bar.value() - anchor_top)
            else:
                bar.setValue(old_value)
    finally:
        widget.blockSignals(was_blocked)


class CompactPanel(DropSurface, QWidget):
    data_changed = Signal()
    intake = Signal(object)
    state_requested = Signal(int, str)
    urgent_requested = Signal(int, bool)
    delivery_requested = Signal(int, object)
    rename_requested = Signal(int, str)
    bulk_requested = Signal(object, object)
    delete_requested = Signal(int)
    restore_deleted_requested = Signal(int)
    workspace_requested = Signal(int)
    scan_requested = Signal()
    approve_requested = Signal(int)
    bundle_requested = Signal(int, object)
    link_requested = Signal(int, int)
    paste_requested = Signal()
    files_requested = Signal()
    folder_requested = Signal(str)
    cancel_requested = Signal()
    quit_requested = Signal()
    background_requested = Signal()

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.busy = False
        self.refreshing = False
        self.current_project = None
        self.project_scope = None
        self._refresh_after_drag = False
        self.today_dialog = None
        self.setObjectName('manager')
        self.setWindowTitle('WorkBox 工作箱 v1.0 · 项目管理')
        self.setWindowIcon(app_icon())
        apply_to(self)
        self.setAcceptDrops(True)
        self.resize(1200, 780)
        self.setMinimumSize(660, 480)
        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.navigation = QFrame()
        self.navigation.setObjectName('sidebar')
        self.navigation.setFixedWidth(164)
        nav = QVBoxLayout(self.navigation)
        nav.setContentsMargins(12, 20, 12, 14)
        nav.setSpacing(8)
        self.brand_row = QHBoxLayout()
        self.brand_row.setContentsMargins(0, 0, 0, 0)
        self.brand_row.setSpacing(6)
        self.brand_icon = QLabel()
        self.brand_icon.setPixmap(app_icon().pixmap(32, 32))
        self.brand_icon.setFixedSize(32, 32)
        self.brand_icon.setAccessibleName('WorkBox 品牌图标')
        self.brand_icon.setToolTip('WorkBox 工作箱')
        self.brand_row.addWidget(self.brand_icon, 0, Qt.AlignHCenter)
        self.brand_label = label('WorkBox', 'brandName')
        self.brand_row.addWidget(self.brand_label)
        nav.addLayout(self.brand_row)
        self.nav_caption = label('工作箱', 'subtle')
        nav.addWidget(self.nav_caption)
        nav.addSpacing(18)
        self.nav_projects = button('项目', 'nav')
        self.nav_projects.setIcon(FluentIcon.FOLDER)
        self.nav_zip = button('ZIP 归档', 'nav')
        self.nav_zip.setIcon(FluentIcon.ZIP_FOLDER)
        self.nav_review = button('待核对', 'nav')
        self.nav_review.setIcon(FluentIcon.EDIT)
        self.today_button = button('今日工作量 0', 'nav')
        self.today_button.setIcon(FluentIcon.CALENDAR)
        self.today_button.setCheckable(False)
        self.today_button.setAccessibleName('今日工作量')
        self.today_button.clicked.connect(self.show_today_deliveries)
        self.nav_controls = [(self.nav_projects, '项目'), (self.today_button, '今日工作量'),
                             (self.nav_zip, 'ZIP 归档'), (self.nav_review, '待核对')]
        for control, caption in self.nav_controls:
            control.setMinimumWidth(0)
            control.setToolTip(caption)
            control.setAccessibleName(caption)
            nav.addWidget(control)
        nav.addStretch()
        shell.addWidget(self.navigation)
        self.workbench = QWidget()
        shell.addWidget(self.workbench, 1)
        layout = self.workbench_layout = QVBoxLayout(self.workbench)
        layout.setContentsMargins(24, 22, 24, 14)
        layout.setSpacing(14)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        brand_row = QHBoxLayout()
        self.page_title = label('项目', 'pageTitle')
        brand_row.addWidget(self.page_title)
        brand_row.addStretch()
        heading.addLayout(brand_row)
        self.summary = label('收项目建目录，ZIP 稳定后复制归档。', 'subtle')
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.summary)
        top.addLayout(heading, 1)
        self.paste_button = button('粘贴消息')
        self.paste_button.setIcon(FluentIcon.PASTE)
        self.files_button = button('收进文件', True)
        self.files_button.setIcon(FluentIcon.ADD)
        self.root_button = button('归档目录')
        self.settings_button = button('设置', 'nav')
        self.settings_button.setAccessibleName('设置')
        self.settings_button.setCheckable(False)
        self.settings_button.setIcon(FluentIcon.SETTING)
        self.appearance_button = button('外观', 'nav-menu')
        self.appearance_button.setCheckable(False)
        self.appearance_button.setAccessibleName('外观主题')
        self.appearance_button.setToolTip('切换浅色、深色或跟随系统，只改变界面外观。')
        theme_menu = QMenu(self.appearance_button)
        self.theme_actions = {}
        theme_group = QActionGroup(theme_menu)
        theme_group.setExclusive(True)
        for key, caption in (('light', '浅色'), ('dark', '深色'), ('system', '跟随系统')):
            action = theme_menu.addAction(caption)
            action.setCheckable(True)
            action.setData(key)
            theme_group.addAction(action)
            action.triggered.connect(lambda _checked=False, mode=key: self.change_theme(mode))
            self.theme_actions[key] = action
        self.appearance_button.setMenu(theme_menu)
        theme_menu.aboutToShow.connect(self.sync_theme_menu)
        theme_signal().connect(self.sync_theme_menu)
        self.sync_theme_menu()
        top.addWidget(self.paste_button)
        top.addWidget(self.files_button)
        self.nav_controls.extend([(self.settings_button, '设置'), (self.appearance_button, '外观')])
        self.appearance_button.setIcon(FluentIcon.PALETTE)
        for control, caption in self.nav_controls[-2:]:
            control.setToolTip(caption)
            control.setMinimumWidth(0)
            nav.addWidget(control)
        layout.addLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索项目、备注、批次或 ZIP 名称')
        self.search.setAccessibleName('搜索项目、备注、批次或 ZIP 名称')
        self.search.setClearButtonEnabled(True)
        intake_bar = QHBoxLayout()
        intake_bar.setSpacing(10)
        intake_bar.addWidget(self.search, 1)
        layout.addLayout(intake_bar)
        self.notice = label('', 'notice')
        self.notice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.notice.setMaximumHeight(72)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('workbenchPages')
        self.tabs.tabBar().hide()
        self.nav_projects.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        self.nav_zip.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        self.nav_review.clicked.connect(lambda: self.tabs.setCurrentIndex(2))
        for control in (self.nav_projects, self.nav_zip, self.nav_review):
            control.clicked.connect(self.sync_navigation)
        self.tabs.currentChanged.connect(self.sync_navigation)
        layout.addWidget(self.tabs, 1)
        projects = QWidget()
        pl = self.project_page_layout = QVBoxLayout(projects)
        pl.setContentsMargins(14, 14, 14, 12)
        pl.setSpacing(12)
        filters = QHBoxLayout()
        self.status_group = QButtonGroup(self)
        self.status_buttons = {}
        for index, state in enumerate((None, *STATUSES)):
            item = button(state or '全部', 'filter')
            item.setObjectName('filter')
            item.setCheckable(True)
            self.status_group.addButton(item, index)
            self.status_buttons[state] = item
            filters.addWidget(item)
            item.clicked.connect(self.refresh)
        self.status_buttons[None].setChecked(True)
        self.status_buttons['待交付'].setToolTip('普通文件成功收录后设为待交付。' + zip_delivery_hint(service.workspaces.settings()))
        self.status_buttons['已交付'].setToolTip(zip_delivery_hint(service.workspaces.settings()))
        filters.addStretch()
        self.batch_filter = QComboBox()
        self.batch_filter.setMaximumWidth(190)
        self.batch_filter.setAccessibleName('批次筛选')
        self.batch_filter.setMinimumWidth(0)
        pl.addLayout(filters)
        views = self.project_filter_layout = QHBoxLayout()
        views.setSpacing(8)
        self.archive_filter = QComboBox()
        self.archive_filter.setAccessibleName('项目归档筛选')
        for caption, value in (('日常项目', False), ('已归档', True), ('全部项目', None), ('已删除', 'deleted')):
            self.archive_filter.addItem(caption, value)
        self.due_filter = QComboBox()
        self.due_filter.setAccessibleName('截止日期筛选')
        for caption, value in (('所有日期', None), ('已逾期', 'overdue'), ('今天截止', 'today'), ('未来7天', 'week'), ('未设日期', 'unscheduled')):
            self.due_filter.addItem(caption, value)
        self.urgent_filter = QCheckBox('只看加急')
        self.bulk_mode = QCheckBox('批量选择')
        self.type_filter = QComboBox()
        self.type_filter.setAccessibleName('项目类型筛选')
        self.type_filter.setMinimumWidth(0)
        self.type_filter.setMaximumWidth(170)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        views.addWidget(self.archive_filter)
        views.addWidget(self.due_filter)
        views.addWidget(self.type_filter)
        views.addWidget(self.batch_filter)
        pl.addLayout(views)
        options = QHBoxLayout()
        options.setSpacing(8)
        options.addWidget(self.urgent_filter)
        options.addStretch()
        self.change_type_button = button('修改类型…')
        self.change_type_button.setAccessibleName('修改所选项目的类型')
        self.change_type_button.setToolTip('修改当前选中项目的类型；批量选择时应用到所选项目。也可管理自定义类型。')
        self.change_type_button.clicked.connect(self.change_selected_project_types)
        options.addWidget(self.change_type_button)
        options.addWidget(self.bulk_mode)
        self.intake_log_button = button('收录记录')
        self.intake_log_button.setToolTip('查看普通文件收录结果、所属项目与判断依据')
        self.intake_log_button.clicked.connect(self.show_intake_log)
        options.addWidget(self.intake_log_button)
        pl.addLayout(options)
        self.project_list = ProjectList()
        self.project_list.setObjectName('projectList')
        self.project_list.setMinimumHeight(72)
        self.project_list.setAccessibleName('项目列表')
        self.project_list.setAccessibleDescription('双击项目打开对应的工作文件夹；右键或按菜单键管理该项目。')
        standard_list_scrolling(self.project_list)
        self.empty = label('还没有项目。把消息拖到悬浮球，或点击「粘贴消息」。', 'subtle')
        self.project_splitter = QSplitter(Qt.Horizontal)
        self.project_splitter.setAccessibleName('项目与文档分栏')
        self.project_splitter.setChildrenCollapsible(False)
        self.project_splitter.setHandleWidth(6)
        project_side = QFrame()
        project_side.setObjectName('projectSurface')
        project_side.setMinimumWidth(180)
        project_layout = self.project_card_layout = QVBoxLayout(project_side)
        project_layout.setContentsMargins(10,10,10,10)
        self.project_list_heading = label('项目清单', 'title')
        project_layout.addWidget(self.project_list_heading)
        self.empty.setWordWrap(True)
        project_layout.addWidget(self.empty)
        project_layout.addWidget(self.project_list,1)
        document_side = self.document_side = QFrame()
        document_side.setObjectName('documentSurface')
        document_side.setMinimumWidth(350)
        document_layout = self.document_card_layout = QVBoxLayout(document_side)
        document_layout.setContentsMargins(14,12,14,10)
        document_layout.setSpacing(10)
        self.project_title = label('选择项目，查看对应文档', 'title', True)
        self.project_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        document_heading = QHBoxLayout()
        document_heading.addWidget(self.project_title, 1)
        self.project_status_badge = InfoBadge('', document_side)
        self.project_status_badge.setAccessibleName('所选项目当前状态')
        self.project_status_badge.hide()
        document_heading.addWidget(self.project_status_badge, 0, Qt.AlignTop)
        document_layout.addLayout(document_heading)
        self.project_type_label = label('', 'subtle', True)
        self.project_type_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.project_type_label.hide()
        document_layout.addWidget(self.project_type_label)
        from project_documents_ui import ProjectDocumentsPane
        self.document_pane = ProjectDocumentsPane(service)
        self.document_pane.folder_requested.connect(self.folder_requested)
        document_layout.addWidget(self.document_pane, 1)
        self.project_splitter.addWidget(project_side)
        self.project_splitter.addWidget(document_side)
        self.project_splitter.setStretchFactor(0,2)
        self.project_splitter.setStretchFactor(1,3)
        self.project_splitter.setSizes([400,600])
        pl.addWidget(self.project_splitter,1)
        self.bulk_bar = QFrame()
        bulk_layout = QVBoxLayout(self.bulk_bar)
        bulk_layout.setContentsMargins(0, 0, 0, 0)
        selection = QHBoxLayout()
        self.selection_count = label('已选 0 项', 'subtle')
        self.select_all_button = button('全选当前结果')
        self.clear_selection_button = button('清空选择')
        selection.addWidget(self.selection_count)
        selection.addStretch()
        selection.addWidget(self.select_all_button)
        selection.addWidget(self.clear_selection_button)
        bulk_layout.addLayout(selection)
        bulk_actions = QHBoxLayout()
        self.bulk_action = QComboBox()
        self.bulk_action.setAccessibleName('对所选项目执行的操作')
        for caption, value in (
            ('设为待开发', 'status:待开发'), ('设为开发中', 'status:开发中'),
            ('设为待交付', 'status:待交付'), ('标记加急', 'urgent'),
            ('取消加急', 'normal'), ('归档所选项目', 'archive'), ('恢复所选项目', 'restore'),
        ):
            self.bulk_action.addItem(caption, value)
        self.bulk_apply = button('应用到所选项目', True)
        bulk_actions.addWidget(self.bulk_action, 1)
        bulk_actions.addWidget(self.bulk_apply)
        bulk_layout.addLayout(bulk_actions)
        self.bulk_bar.hide()
        pl.addWidget(self.bulk_bar)
        self.tabs.addTab(projects, '项目')
        zips = QWidget()
        zl = QVBoxLayout(zips)
        zl.setContentsMargins(16, 16, 16, 12)
        self.unmatched = QCheckBox('只看待关联 ZIP')
        zip_head = QHBoxLayout()
        zip_head.addWidget(self.unmatched)
        zip_head.addStretch()
        self.scan_button = button('检查归档')
        self.sources_button = button('监视详情')
        zip_head.addWidget(self.sources_button)
        zip_head.addWidget(self.scan_button)
        zip_head.addWidget(self.root_button)
        zl.addLayout(zip_head)
        self.archive_status = label('', 'subtle')
        self.archive_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        zl.addWidget(self.archive_status)
        self.zip_list = QListWidget()
        self.zip_list.setAccessibleName('ZIP 文件列表')
        zl.addWidget(self.zip_list, 1)
        row = QHBoxLayout()
        self.link_button = button('选择对应项目', True)
        self.zip_locate = button('定位文件夹')
        self.approve_button = button('标记可用版本')
        self.recognize_button = button('识别包内项目')
        row.addWidget(self.link_button)
        row.addWidget(self.recognize_button)
        row.addWidget(self.approve_button)
        row.addWidget(self.zip_locate)
        row.addStretch()
        zl.addLayout(row)
        self.zip_policy_note = label('', 'subtle', True)
        zl.addWidget(self.zip_policy_note)
        self.tabs.addTab(zips, 'ZIP 库')
        pending = QWidget()
        rl = QVBoxLayout(pending)
        rl.setContentsMargins(16, 16, 16, 12)
        rl.addWidget(label('只有不明确的文字需要核对；原文会保留到你处理。', 'subtle'))
        self.receipt_list = QListWidget()
        self.receipt_list.setAccessibleName('待核对文字')
        rl.addWidget(self.receipt_list, 1)
        review_row = QHBoxLayout()
        self.review_button = button('核对并收录', True)
        self.dismiss_button = button('不是项目，忽略')
        review_row.addWidget(self.review_button)
        review_row.addWidget(self.dismiss_button)
        review_row.addStretch()
        rl.addLayout(review_row)
        self.tabs.addTab(pending, '待核对')
        footer = QHBoxLayout()
        footer_hint = self.footer_hint = label('后台运行保留托盘；退出将停止自动归档。', 'subtle')
        footer_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer_hint.setToolTip(footer_hint.text())
        footer.addWidget(footer_hint, 1)
        footer.addStretch()
        self.help_button = button('使用指南')
        self.help_button.setAccessibleName('使用指南')
        self.help_button.setObjectName('使用指南')
        self.help_button.setToolTip('查看当前模式、保存位置和操作方法，不改变设置。')
        self.help_button.clicked.connect(self.show_quick_start)
        footer.addWidget(self.help_button)
        self.cancel_button = button('取消处理')
        self.cancel_button.hide()
        self.exit_button = button('退出')
        self.exit_button.setToolTip('完全关闭 WorkBox，停止自动归档；下次需重新启动。')
        self.background_button = button('后台运行')
        self.background_button.setToolTip('隐藏面板和悬浮球，继续自动归档；可从系统托盘重新打开。')
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.background_button)
        footer.addWidget(self.exit_button)
        layout.addLayout(footer)
        self.paste_button.clicked.connect(self.paste_requested)
        self.files_button.clicked.connect(self.files_requested)
        self.root_button.clicked.connect(lambda: self.folder_requested.emit(service.workspaces.settings()['archive_root']))
        self.settings_button.clicked.connect(self.edit_directories)
        self.scan_button.clicked.connect(self.scan_requested)
        self.sources_button.clicked.connect(self.show_sources)
        self.approve_button.clicked.connect(self.approve_package)
        self.recognize_button.clicked.connect(self.recognize_package)
        self.zip_list.currentRowChanged.connect(self.update_actions)
        self.exit_button.clicked.connect(self.quit_requested)
        self.background_button.clicked.connect(self.background_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.search.textChanged.connect(self.refresh)
        self.batch_filter.currentIndexChanged.connect(self.refresh)
        self.unmatched.toggled.connect(self.refresh)
        self.project_list.itemSelectionChanged.connect(self.select_project)
        self.project_list.itemDoubleClicked.connect(
            lambda item: self.workspace_requested.emit(item.data(Qt.UserRole)))
        self.project_list.project_context_requested.connect(self.show_project_context_menu)
        self.archive_filter.currentIndexChanged.connect(self.refresh)
        self.due_filter.currentIndexChanged.connect(self.refresh)
        self.urgent_filter.toggled.connect(self.refresh)
        self.bulk_mode.toggled.connect(self.toggle_bulk)
        self.select_all_button.clicked.connect(self.project_list.selectAll)
        self.clear_selection_button.clicked.connect(self.project_list.clearSelection)
        self.bulk_apply.clicked.connect(self.apply_bulk)
        self.link_button.clicked.connect(self.link_package)
        self.zip_locate.clicked.connect(self.locate_zip)
        self.review_button.clicked.connect(self.review_text)
        self.receipt_list.itemDoubleClicked.connect(self.review_text)
        self.dismiss_button.clicked.connect(self.dismiss_text)
        for view in (self.zip_list, self.receipt_list):
            standard_list_scrolling(view)
        for view in (self.project_list, self.zip_list, self.receipt_list):
            view.verticalScrollBar().sliderReleased.connect(self._finish_scroll)
        self.refresh()
        self.sync_navigation()

    def sync_navigation(self, *_):
        index = self.tabs.currentIndex()
        self.page_title.setText(('项目', 'ZIP 归档', '待核对')[max(0, index)])
        for position, control in enumerate((self.nav_projects, self.nav_zip, self.nav_review)):
            control.setChecked(position == index)

    def refresh_type_controls(self):
        selected_type = self.type_filter.currentData()
        types = self.service.list_project_types(include_inactive=True) if hasattr(self.service, 'list_project_types') else []
        with QSignalBlocker(self.type_filter):
            self.type_filter.clear()
            self.type_filter.addItem('所有类型', None)
            self.type_filter.addItem('未分类', 0)
            for item in types:
                inactive = ' · 已停用' if not item['active'] else ''
                self.type_filter.addItem(f"{item['name']} ({item.get('project_count', 0)}){inactive}", item['id'])
                self.type_filter.setItemData(self.type_filter.count() - 1, item['name'] + inactive, Qt.ToolTipRole)
            self.type_filter.setCurrentIndex(max(0, self.type_filter.findData(selected_type)))
        action = self.bulk_action.currentData()
        with QSignalBlocker(self.bulk_action):
            for index in reversed(range(self.bulk_action.count())):
                if str(self.bulk_action.itemData(index)).startswith('type:'):
                    self.bulk_action.removeItem(index)
            self.bulk_action.addItem('项目类型：未分类', 'type:0')
            for item in types:
                if item['active']:
                    self.bulk_action.addItem('项目类型：' + item['name'], f"type:{item['id']}")
            self.bulk_action.setCurrentIndex(max(0, self.bulk_action.findData(action)))

    def refresh(self, *_):
        if any(view.verticalScrollBar().isSliderDown() for view in (self.project_list, self.zip_list, self.receipt_list)):
            self._refresh_after_drag = True
            return
        self._refresh_after_drag = False
        self.refreshing = True
        try:
            self.refresh_type_controls()
            batch = self.batch_filter.currentData()
            self.batch_filter.blockSignals(True)
            self.batch_filter.clear()
            self.batch_filter.addItem('所有批次', None)
            for item in self.service.list_batches():
                self.batch_filter.addItem(item['name'], item['id'])
            self.batch_filter.setCurrentIndex(max(0, self.batch_filter.findData(batch)))
            self.batch_filter.blockSignals(False)
            status = next((s for s, b in self.status_buttons.items() if b.isChecked()), None)
            scope = (self.search.text(), status, self.batch_filter.currentData(), self.archive_filter.currentData(), self.due_filter.currentData(), self.urgent_filter.isChecked(), self.type_filter.currentData())
            deleted_view = scope[3] == 'deleted'
            if deleted_view and self.bulk_mode.isChecked():
                self.bulk_mode.setChecked(False)
            type_options = {'type_id': scope[6]} if scope[6] is not None else {}
            rows = self.service.list_projects(scope[0], scope[1], scope[2], scope[5],
                archived=None if deleted_view else scope[3], due_filter=scope[4], deleted=deleted_view, **type_options)
            populate(self.project_list, rows, self.project_caption, clear_selection=scope != self.project_scope)
            self.project_scope = scope
            self.empty.setVisible(not rows)
            self.empty.setText('没有匹配的项目，可调整日期、归档或状态筛选。' if self.service.db.counts()['projects'] else '还没有项目。把消息拖到悬浮球，或点击「粘贴消息」。')
            zips = self.service.list_zips(self.search.text(), self.unmatched.isChecked())
            versions = {v['zip_id']: v for v in self.service.snapshots.list_versions()}
            ordinal, totals = {}, {}
            for version in sorted(versions.values(), key=lambda v: v['zip_id']):
                key = version['project_id']
                totals[key] = totals.get(key, 0) + 1
                ordinal[version['zip_id']] = totals[key]
            populate(self.zip_list, zips, lambda z: self.zip_caption(z, versions.get(z['id'], {}), ordinal.get(z['id'])))
            for index in range(self.zip_list.count()):
                item = self.zip_list.item(index)
                version = versions.get(item.data(Qt.UserRole))
                if version:
                    details = self.zip_count_details(version)
                    item.setToolTip('来源：' + version['source_path'] + '\n归档：' + version['path'] + ('\n合包核对：' + version['bundle_reason'] if version.get('bundle_reason') else '') + ('\n' + details if details else ''))
            self.refresh_archive_status()
            receipts = self.service.pending_receipts()
            populate(self.receipt_list, receipts, lambda r: r['raw_text'][:70].replace('\n', ' ') + '\n' + r['reason'])
            self.tabs.setTabText(2, f'待核对 {len(receipts)}' if receipts else '待核对')
            self.nav_review.setToolTip(f'待核对 {len(receipts)}' if receipts else '待核对')
            counts = self.service.db.counts()
            self.summary.setText(f"共 {counts['projects']} 个项目 · 当前 {len(rows)} 项 · {counts['zips']} 个交付包")
            if deleted_view:
                self.summary.setText(f'已删除 {len(rows)} 项 · 原文件、归档和交付记录保留')
        finally:
            self.refreshing = False
        self.select_project()

    def refresh_archive_status(self):
        sources = self.service.snapshots.list_sources()
        errors = sum(s.get('state') == 'error' for s in sources)
        pending_count = sum(s.get('state') == 'pending' for s in sources)
        settings = self.service.workspaces.settings()
        delay = settings['stable_seconds']
        self.archive_status.setText(f'稳定 {delay} 秒后归档 · {pending_count} 个等待 · {errors} 个异常 · {len(sources)} 个来源')
        self.archive_status.setToolTip('仅在软件运行时监视登记目录及主动提交的 ZIP。异常详情可在「监视详情」查看。')
        self.status_buttons['待交付'].setToolTip('普通文件成功收录后设为待交付。' + zip_delivery_hint(settings))
        self.status_buttons['已交付'].setToolTip(zip_delivery_hint(settings) + ' 也可通过项目右键菜单人工确认。')
        self.zip_policy_note.setText('原 ZIP 保留，归档保留原名；新内容另存版本。' + zip_delivery_hint(settings))
        self.refresh_today_count()

    def refresh_today_count(self):
        summary = self.service.db.today_delivery_summary()
        self._today_caption = f"今日工作量 {summary['project_count']}"
        self._today_compact_caption = str(summary['project_count']) if summary['project_count']<100 else '99+'
        self.today_button.setAccessibleDescription(self._today_caption)
        self.resize_navigation()
        self.today_button.setToolTip(f"{summary['day']}（北京时间）：{summary['project_count']} 个首次交付项目计入个人工作量，{summary['event_count']} 次交付记录。每个项目只计首次有效交付；补交只保留记录。代交项目可排除个人工作量并随时恢复，项目的已交付状态不变。点击查看并管理。")

    def show_today_deliveries(self):
        from delivery_ui import TodayDeliveryDialog
        if self.today_dialog is None:
            self.today_dialog = TodayDeliveryDialog(self.service, self)
            self.today_dialog.folder_requested.connect(self.folder_requested)
            self.today_dialog.statistics_changed.connect(self.refresh_today_count)
            self.today_dialog.statistics_changed.connect(self.data_changed.emit)
            self.today_dialog.operation_completed.connect(self.show_notice)
        self.today_dialog.refresh()
        self.today_dialog.show()
        self.today_dialog.raise_()
        self.today_dialog.activateWindow()

    def _finish_scroll(self):
        if self._refresh_after_drag:
            self.refresh()

    def focus_projects(self, project_ids):
        """仅在用户点击本次收录的查看入口时清理筛选并定位，不改变项目数据。"""
        all_projects = {row['id']: row for row in self.service.list_projects(archived=None)}
        ids = list(dict.fromkeys(pid for pid in project_ids if pid in all_projects))
        if not ids:
            self.show_notice('这次收录的项目已不存在，未跳转到其他项目。')
            return False
        controls = (self.search, self.batch_filter, self.archive_filter, self.due_filter, self.type_filter,
                    self.urgent_filter, self.bulk_mode, *self.status_buttons.values())
        blockers = [QSignalBlocker(control) for control in controls]
        self.search.clear()
        self.status_buttons[None].setChecked(True)
        self.due_filter.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0)
        self.urgent_filter.setChecked(False)
        self.archive_filter.setCurrentIndex(2 if any(all_projects[pid].get('archived_at') for pid in ids) else 0)
        batches = {all_projects[pid]['batch_id'] for pid in ids}
        batch_id = next(iter(batches)) if len(ids) > 1 and len(batches) == 1 else None
        if batch_id is not None and self.batch_filter.findData(batch_id) < 0:
            self.batch_filter.addItem(all_projects[ids[0]]['batch_name'], batch_id)
        self.batch_filter.setCurrentIndex(max(0, self.batch_filter.findData(batch_id)))
        self.bulk_mode.setChecked(False)
        del blockers
        self.toggle_bulk(False)
        self.tabs.setCurrentIndex(0)
        self.refresh()
        item = next((self.project_list.item(i) for i in range(self.project_list.count()) if self.project_list.item(i).data(Qt.UserRole) == ids[0]), None)
        if item is None:
            self.show_notice('项目列表正在更新，请再次点击查看。')
            return False
        self.project_list.setCurrentItem(item)
        self.project_list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.project_list.setFocus(Qt.OtherFocusReason)
        self.select_project()
        self.show_notice(f'已定位本次收录的 {len(ids)} 个项目，当前选中：' + all_projects[ids[0]]['name'])
        return True

    def select_project(self, *_):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            items = self.project_list.selectedItems()
            pid = items[0].data(Qt.UserRole) if len(items) == 1 else None
            self.current_project = self.service.get_project(pid) if pid is not None else None
            p = self.current_project
            self.project_title.setText(p['name'] if p else '选择一个项目，查看对应文档')
            self.project_type_label.setText('类型 · ' + (p.get('project_type_name') or '未分类') if p else '')
            self.project_type_label.setVisible(p is not None)
            self.project_status_badge.setVisible(p is not None)
            if p is not None:
                self.project_status_badge.setText(p['status'])
                levels = {'待开发': InfoLevel.INFOAMTION, '开发中': InfoLevel.ATTENTION,
                          '待交付': InfoLevel.WARNING, '已交付': InfoLevel.SUCCESS}
                self.project_status_badge.setLevel(levels[p['status']])
            self.project_title.setToolTip((p['name'] + '\n' + (p.get('workspace') or {}).get('path', '工作目录尚未创建')) if p else '')
        finally:
            self.refreshing = False
        self.update_actions()
        self.document_pane.set_project(self.current_project['id'] if self.current_project else None)

    def update_actions(self, *_):
        for control in (self.link_button, self.review_button, self.dismiss_button):
            control.setEnabled(not self.busy)
        count = len(self.project_list.selectedItems())
        self.selection_count.setText(f'已选 {count} 项 / 当前 {self.project_list.count()} 项')
        bulk_allowed = not self.busy and self.archive_filter.currentData() != 'deleted'
        self.bulk_mode.setEnabled(bulk_allowed)
        self.bulk_apply.setEnabled(bool(count) and count <= 500 and bulk_allowed)
        self.bulk_action.setEnabled(bulk_allowed)
        type_editable = bulk_allowed and 1 <= count <= 500 and hasattr(self.service, 'set_project_types')
        if type_editable:
            try:
                type_editable = all(not p.get('archived_at') and not p.get('deleted_at') for p in
                                    (self.service.get_project(item.data(Qt.UserRole)) for item in self.project_list.selectedItems()))
            except KeyError:
                type_editable = False
        self.change_type_button.setEnabled(type_editable)
        self.settings_button.setEnabled(not self.busy)
        self.scan_button.setEnabled(not self.busy)
        zid = selected(self.zip_list)
        bundle = self.service.db.get_zip_bundle(zid) if zid is not None else None
        self.approve_button.setEnabled(not self.busy and zid is not None and self.service.snapshots.is_snapshot(zid) and not (bundle and bundle['state'] == 'needs-review'))
        self.recognize_button.setEnabled(not self.busy and zid is not None and bool(self.service.db.get_zip_bundle(zid) or self.service.is_bundle_candidate(self.service.db.get_zip(zid)['filename'])))

    def set_busy(self, busy):
        self.busy = busy
        self.cancel_button.setVisible(busy)
        self.update_actions()

    def _action_project(self, project_id, *, editable=False):
        if self.busy:
            return None
        try:
            project = self.service.get_project(project_id)
        except KeyError:
            return None
        return None if editable and project.get('archived_at') else project

    def project_context_menu(self, project_id):
        try:
            project = self.service.get_project(project_id)
        except KeyError:
            return None
        menu = QMenu(self)
        menu.setAccessibleName(project['name'] + ' · 项目操作')
        heading = menu.addSection(menu.fontMetrics().elidedText(project['name'],Qt.ElideRight,320).replace('&','&&'))
        heading.setToolTip(project['name'])
        active = not self.busy
        editable = active and not project.get('archived_at')

        def add(parent, text, callback, enabled=True):
            item = parent.addAction(text)
            item.setEnabled(bool(enabled))
            item.triggered.connect(callback)
            return item

        add(menu, '工作目录' if project.get('workspace') else '建工作目录',
            lambda: self.open_project_workspace(project_id), active and (editable or project.get('workspace')))
        states = menu._states_menu = QMenu('开发状态', menu)
        menu.addMenu(states)
        states.setEnabled(bool(editable))
        group = menu._state_group = QActionGroup(states)
        group.setExclusive(True)
        for state in STATUSES[:3]:
            item = add(states, state, lambda _checked=False, value=state: self.change_state(project_id, value), editable)
            item.setCheckable(True)
            item.setChecked(state == project['status'])
            group.addAction(item)
        urgent = add(menu, '加急', lambda checked: self.change_urgent(project_id, checked), editable)
        urgent.setCheckable(True)
        urgent.setChecked(bool(project['urgent']))
        if hasattr(self.service, 'list_project_types'):
            type_menu = menu._type_menu = QMenu('项目类型', menu)
            menu.addMenu(type_menu)
            type_menu.setEnabled(editable and not project.get('deleted_at'))
            type_group = type_menu._type_group = QActionGroup(type_menu)
            type_group.setExclusive(True)
            available_types = self.service.list_project_types()
            choices = [{'id': None, 'name': '未分类'}, *available_types]
            current_type = project.get('project_type_id')
            if current_type and not any(item['id'] == current_type for item in available_types):
                choices.append({'id': current_type, 'name': (project.get('project_type_name') or '原类型') + '（已停用）', 'inactive': True})
            for kind in choices:
                item = add(type_menu, kind['name'].replace('&','&&'),
                           lambda _checked=False, type_id=kind['id']: self.assign_project_types([project_id], type_id),
                           editable and not kind.get('inactive'))
                item.setData(kind['id'])
                item.setCheckable(True)
                item.setChecked(kind['id'] == current_type)
                type_group.addAction(item)
        menu.addSeparator()
        add(menu, '查看备注与日期…' if project.get('archived_at') else '备注与日期…',
            lambda: self.edit_details(project_id), active)
        add(menu, '改项目名…', lambda: self.rename_project(project_id), editable)
        add(menu, '看原文', lambda: self.show_source(project_id), active)
        menu.addSeparator()
        delivery = add(menu, '确认交付…', lambda: self.confirm_delivery(project_id), editable and project['status'] == '待交付')
        delivery.setToolTip('选择归档项目工作文件，或明确指定一个 ZIP 版本；首次交付计入当天统计，补交只保留记录。')
        positions = menu._positions_menu = QMenu('ZIP 位置', menu)
        menu.addMenu(positions)
        positions.setEnabled(active and bool(project['zips']))
        for index, version in enumerate(project['zips']):
            item = add(positions, self.project_version_caption(project, version, index),
                       lambda _checked=False, zid=version['id']: self.locate_project_zip(project_id, zid), active)
            item.setToolTip(version['path'])
        menu.addSeparator()
        if project.get('deleted_at'):
            add(menu, '恢复已删除项目', lambda: self.restore_deleted_project(project_id), active)
        else:
            add(menu, '恢复项目' if project.get('archived_at') else '归档项目',
                lambda: self.archive_project(project_id), active)
            add(menu, '删除项目…', lambda: self.delete_project(project_id), active)
        return menu

    def show_project_context_menu(self, project_id, position):
        menu = self.project_context_menu(project_id)
        if menu is not None:
            try:
                menu.exec(position)
            finally:
                menu.deleteLater()

    @staticmethod
    def project_version_caption(project, version, index):
        prefix = '已交付 · ' if version['id'] == project['delivered_zip_id'] else '可用版 · ' if version.get('approved') else ''
        return prefix + f"V{len(project['zips'])-index} · " + version['filename']

    def open_project_workspace(self, project_id):
        project = self._action_project(project_id)
        if project and (not project.get('archived_at') or project.get('workspace')):
            self.workspace_requested.emit(project_id)

    def change_state(self, project_id, state):
        project = self._action_project(project_id, editable=True)
        if project and state in STATUSES[:3] and state != project['status']:
            self.state_requested.emit(project_id, state)

    def change_urgent(self, project_id, urgent):
        project = self._action_project(project_id, editable=True)
        if project:
            self.urgent_requested.emit(project_id, urgent)

    @staticmethod
    def zip_count_details(version, limit=None):
        if version.get('bundle_state') == 'needs-review':
            return ''
        counts = version.get('file_counts') or []
        if not counts:
            return ''
        shown = counts if limit is None else counts[:limit]
        parts = [f"{fact['project_name']} {fact['file_count']} 个文件 · {fact['decided_status']}" for fact in shown]
        if len(shown) < len(counts):
            parts.append(f'等 {len(counts)} 个项目')
        return '归档时：' + '；'.join(parts)

    @staticmethod
    def zip_caption(record, version, ordinal):
        prefix = ''
        if version.get('bundle_count'):
            prefix += f"{version['bundle_count']} 项目合包 · "
        if version.get('bundle_state') == 'needs-review':
            prefix += '待核对 · '
        if version.get('approved'):
            prefix += (f"可用 {version['approved_count']}/{version['project_count']} 项 · "
                       if version.get('project_count', 0) > 1 else '可用版 · ')
        if ordinal:
            prefix += f'V{ordinal} · '
        caption = prefix + record['filename'] + '\n' + f"{record['project_name'] or '待关联'}    {human_size(record['size'])}    {local_timestamp(record['created_at'])}"
        details = CompactPanel.zip_count_details(version, limit=3)
        return prefix + record['filename'] + '\n' + details if details else caption

    @staticmethod
    def project_caption(project):
        parts = [project['status'], project['batch_name']]
        if project.get('project_type_name'):
            parts.append('类型：' + project['project_type_name'])
        if project.get('due_date'):
            due = date.fromisoformat(project['due_date'])
            days = (date.today() - due).days
            caption = f"截止 {project['due_date']}"
            if project['status'] != '已交付' and not project.get('archived_at'):
                caption = f'逾期 {days} 天' if days > 0 else ('今天截止' if days == 0 else caption)
            parts.append(caption)
        if project.get('notes'):
            parts.append('有备注')
        if project.get('deleted_at'):
            parts.append('已删除')
        elif project.get('archived_at'):
            parts.append('已归档')
        parts.append(f"{project['zip_count']} 个交付包")
        return f"{'加急  ' if project['urgent'] else ''}{project['name']}\n" + '    '.join(parts)

    def toggle_bulk(self, enabled):
        self.project_list.clearSelection()
        self.project_list.setSelectionMode(QAbstractItemView.MultiSelection if enabled else QAbstractItemView.SingleSelection)
        self.document_side.setVisible(not enabled)
        self.bulk_bar.setVisible(enabled)
        self.select_project()

    def edit_details(self, project_id):
        project = self._action_project(project_id)
        if project:
            with transient_dialog(ProjectDetailsDialog(self.service, project, self)) as dialog:
                if dialog.exec() == QDialog.Accepted:
                    self.refresh()
                    self.data_changed.emit()
                    self.show_notice('项目备注与截止日期已保存。')

    def archive_project(self, project_id):
        project = self._action_project(project_id)
        if not project or project.get('deleted_at'):
            return
        archived = not bool(project.get('archived_at'))
        self.request_bulk([project_id], {'archived': archived})

    def delete_project(self, project_id):
        project = self._action_project(project_id)
        if not project or project.get('deleted_at'):
            return
        answer = QMessageBox.question(self, '删除项目',
            '将项目“'+project['name']+'”移入已删除？\n\n'
            '原文件、工作目录、归档和历史交付记录及统计均保留。\n'
            '可在项目筛选中的“已删除”里恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.delete_requested.emit(project_id)

    def restore_deleted_project(self, project_id):
        project = self._action_project(project_id)
        if project and project.get('deleted_at'):
            self.restore_deleted_requested.emit(project_id)

    def apply_bulk(self):
        ids = [item.data(Qt.UserRole) for item in self.project_list.selectedItems()]
        if not ids or self.busy or self.archive_filter.currentData() == 'deleted':
            return
        action = self.bulk_action.currentData()
        if action.startswith('type:'):
            type_id = int(action.split(':', 1)[1])
            self.assign_project_types(ids, type_id or None)
            return
        if action.startswith('status:'):
            options = {'status': action.split(':', 1)[1]}
        elif action in ('urgent', 'normal'):
            options = {'urgent': action == 'urgent'}
        else:
            options = {'archived': action == 'archive'}
        self.request_bulk(ids, options)

    def assign_project_types(self, project_ids, type_id):
        if self.busy or not project_ids:
            return
        # 服务层原子校验仍是最终边界，触发时重新检查操作对象。
        if any(not self._action_project(pid, editable=True) or self.service.get_project(pid).get('deleted_at')
               for pid in project_ids):
            self.show_notice('项目类型未保存：请先恢复已归档或已删除的项目。')
            return
        try:
            self.service.set_project_types(project_ids, type_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as error:
            self.show_notice('项目类型未保存：' + str(error))
            return
        self.refresh()
        self.data_changed.emit()
        self.show_notice(f'已更新 {len(project_ids)} 个项目的类型。')

    def change_selected_project_types(self):
        from project_types_ui import choose_project_type
        if self.busy:
            return
        ids = [item.data(Qt.UserRole) for item in self.project_list.selectedItems()]
        if not 1 <= len(ids) <= 500:
            return
        try:
            projects = [self.service.get_project(pid) for pid in ids]
        except KeyError:
            self.show_notice('项目已变化，请重新选择。')
            return
        if any(p.get('archived_at') or p.get('deleted_at') for p in projects):
            self.show_notice('项目类型未保存：请先恢复已归档或已删除的项目。')
            return
        accepted, type_id = choose_project_type(self.service, self, projects)
        # 字典管理即时保存，外层取消也应刷新新名称与筛选选项。
        self.refresh()
        if accepted:
            self.assign_project_types(ids, type_id)

    def request_bulk(self, ids, options):
        if self.busy:
            return
        if options.get('archived'):
            answer = QMessageBox.question(self, '归档项目', f'归档所选 {len(ids)} 个项目？\n\n项目会移出日常列表，备注、截止日期、交付记录和 ZIP 均保留，可随时恢复。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        elif 'status' in options and any(self.service.get_project(pid)['status'] == '已交付' for pid in ids):
            answer = QMessageBox.question(self, '重新开始项目', '所选项目包含已交付项目。修改状态会清除这些项目当前的交付确认，ZIP 文件仍保留。\n\n继续修改？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        if not self.busy:
            self.bulk_requested.emit(ids, options)

    def confirm_delivery(self, project_id):
        project = self._action_project(project_id, editable=True)
        if not project or project['status'] != '待交付':
            return
        with transient_dialog(ProjectDeliveryDialog(project, self)) as dialog:
            if dialog.exec() != QDialog.Accepted:
                return
            project = self._action_project(project_id, editable=True)
            if not project or project['status'] != '待交付':
                return
            zid = dialog.version_box.currentData()
            version = next((version for version in project['zips'] if version['id'] == zid), None)
            if zid is None or (version and version.get('bundle_state') != 'needs-review'):
                self.delivery_requested.emit(project_id, zid)

    def locate_project_zip(self, project_id, zip_id):
        project = self._action_project(project_id)
        version = next((version for version in project['zips'] if version['id'] == zip_id), None) if project else None
        if version:
            self.folder_requested.emit(str(Path(version['path']).parent))

    def locate_zip(self):
        zid = selected(self.zip_list)
        if zid is not None:
            self.folder_requested.emit(str(Path(self.service.db.get_zip(zid)['path']).parent))

    def rename_project(self, project_id):
        project = self._action_project(project_id, editable=True)
        if project:
            name, accepted = QInputDialog.getText(self, '修正项目名称', '项目名称', text=project['name'])
            if accepted and self._action_project(project_id, editable=True):
                self.rename_requested.emit(project_id, name)

    def show_source(self, project_id):
        project = self._action_project(project_id)
        if project:
            dialog = QDialog(self)
            dialog.setWindowTitle('收录原文')
            dialog.resize(560, 400)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit(project['raw_text'])
            text.setReadOnly(True)
            layout.addWidget(text)
            close = button('关闭')
            close.clicked.connect(dialog.accept)
            layout.addWidget(close)
            with transient_dialog(dialog):
                dialog.exec()

    def link_package(self):
        zid = selected(self.zip_list)
        if zid is None:
            self.show_notice('请先选择一个 ZIP。')
            return
        if self.service.db.get_zip_bundle(zid) or self.service.is_bundle_candidate(self.service.db.get_zip(zid)['filename']):
            self.recognize_package()
            return
        project_id = self.choose_file_project('交付包：' + self.service.db.get_zip(zid)['filename'])
        if project_id is not None:
            self.link_requested.emit(zid, project_id)

    def choose_file_project(self, filename):
        dialog = QDialog(self)
        dialog.setWindowTitle('选择文件对应的项目')
        dialog.resize(560, 410)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(filename, 'title', True))
        layout.addWidget(label('无法唯一匹配时，请明确选择。原文件保持不动。', 'subtle'))
        search = QLineEdit()
        search.setAccessibleName('查找文件对应的项目')
        search.setClearButtonEnabled(True)
        search.setPlaceholderText('搜索项目或批次')
        choices = QListWidget()
        standard_list_scrolling(choices)
        layout.addWidget(search)
        layout.addWidget(choices, 1)
        def load():
            populate(choices, self.service.list_projects(search.text()), lambda p: p['name'] + '\n' + p['batch_name'])
        search.textChanged.connect(load)
        load()
        confirm = button('选择此项目', True)
        confirm.setEnabled(False)
        choices.itemSelectionChanged.connect(lambda: confirm.setEnabled(bool(choices.selectedItems())))
        layout.addWidget(confirm)
        confirm.clicked.connect(lambda: dialog.accept() if selected(choices) is not None else None)
        with transient_dialog(dialog):
            if dialog.exec() == QDialog.Accepted:
                return selected(choices)
        return None

    def approve_package(self):
        zid = selected(self.zip_list)
        if zid is None or self.busy:
            return
        count = len(self.service.db.zip_project_ids(zid))
        answer = QMessageBox.question(self, '标记可用版本', f'你已检查这个版本并确认可用？\n\n将设为关联的 {count} 个项目的可用版本。该标记不会确认交付，后续新归档不会替换此标记。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.approve_requested.emit(zid)

    def recognize_package(self):
        zid = selected(self.zip_list)
        if zid is None or self.busy:
            return
        record = self.service.db.get_zip(zid)
        bundle = self.service.db.get_zip_bundle(zid)
        dialog = QDialog(self)
        dialog.setWindowTitle('核对多项目 ZIP')
        dialog.resize(600, 380)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(record['filename'], 'title', True))
        layout.addWidget(label('按原始文件名和包内目录核对。当前新版本规则：' + zip_delivery_hint(self.service.workspaces.settings()) +
            ' 已有归档保留原判定；待核对时不猜测项目状态。', 'subtle', True))
        members = QPlainTextEdit()
        members.setReadOnly(True)
        members.setPlainText(('\n'.join(bundle['member_names']) + '\n\n' + bundle['reason']) if bundle else '此包尚未识别。点击下方按钮读取包内项目名称。')
        layout.addWidget(members, 1)
        batch = QComboBox()
        batch.setAccessibleName('合包所属批次')
        batch.addItem('自动核对唯一批次', None)
        for row in self.service.list_batches():
            batch.addItem(row['name'], row['id'])
        layout.addWidget(batch)
        row = QHBoxLayout()
        row.addStretch()
        cancel = button('取消')
        confirm = button('识别并更新项目', True)
        cancel.clicked.connect(dialog.reject)
        confirm.clicked.connect(dialog.accept)
        row.addWidget(cancel)
        row.addWidget(confirm)
        layout.addLayout(row)
        with transient_dialog(dialog):
            if dialog.exec() == QDialog.Accepted:
                self.bundle_requested.emit(zid, batch.currentData())

    def edit_directories(self):
        with transient_dialog(DirectorySettingsDialog(self.service, self)) as dialog:
            if dialog.exec() == QDialog.Accepted:
                self.refresh()
                self.show_notice('工作设置已保存。已有文件、历史判定和交付记录保留。')
            elif getattr(dialog, 'types_changed', False):
                self.refresh()
            if getattr(dialog, 'types_changed', False):
                self.data_changed.emit()

    def show_quick_start(self):
        from onboarding_ui import QuickStartDialog
        with transient_dialog(QuickStartDialog(self.service, self)) as dialog:
            dialog.exec()

    def show_intake_log(self):
        with transient_dialog(FileIntakeLogDialog(self.service, self)) as dialog:
            dialog.exec()

    def show_sources(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('ZIP 自动归档监视')
        dialog.resize(650, 420)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        states = {'pending': '等待文件稳定并归档', 'archived': '最近内容已归档', 'paused': '项目已归档或来源已暂停', 'error': '暂未归档', 'copy-retained': '工作副本未修改'}
        text.setPlainText('\n\n'.join(s['source_path'] + '\n' + states.get(s.get('state'), '等待检查') + ('\n' + s['last_error'] if s['last_error'] else '') for s in self.service.snapshots.list_sources()) or '还没有 ZIP 来源。项目工作目录内的新 ZIP 会自动发现，也可主动拖入。')
        layout.addWidget(text)
        close = button('关闭')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        with transient_dialog(dialog):
            dialog.exec()

    def review_text(self, *_):
        rid = selected(self.receipt_list)
        row = next((r for r in self.service.pending_receipts() if r['id'] == rid), None)
        if row:
            with transient_dialog(ReviewDialog(self.service, row, self)) as dialog:
                dialog.exec()
            self.refresh()
            self.data_changed.emit()

    def dismiss_text(self):
        rid = selected(self.receipt_list)
        if rid is not None:
            self.service.dismiss_receipt(rid)
            self.refresh()
            self.data_changed.emit()

    def show_notice(self, message):
        self.notice.setText(message.splitlines()[0] if message else '')
        self.notice.setToolTip(message)
        self.notice.setVisible(bool(message))

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def sync_theme_menu(self, *_):
        for mode, action in self.theme_actions.items():
            action.setChecked(mode == current_mode())

    def change_theme(self, mode):
        try:
            set_theme(mode, self.service)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.show_notice('外观未保存，原主题保留：' + str(error))
        self.sync_theme_menu()

    def resize_navigation(self):
        narrow = self.width() < 900
        caption = getattr(self, '_today_caption', '今日工作量 0')
        control_width = max(140, self.today_button.fontMetrics().horizontalAdvance(caption) + 52)
        self.navigation.setFixedWidth(72 if narrow else control_width + 24)
        self.navigation.layout().setContentsMargins(8 if narrow else 12, 14, 8 if narrow else 12, 14)
        self.brand_label.setVisible(not narrow)
        self.nav_caption.setVisible(not narrow)
        for control, caption in self.nav_controls:
            control.setProperty('compactNav', narrow)
            text = getattr(self, '_today_caption', caption) if control is self.today_button else caption
            if narrow:
                text = ''
            if control is self.today_button:
                control.setCompactCount(getattr(self, '_today_compact_caption', '0') if narrow else None)
            control.setText(text)
            control.setFixedWidth(56 if narrow else control_width)
            control.style().unpolish(control)
            control.style().polish(control)

    def resizeEvent(self, event):
        # 小窗口优先保留操作控件和可滚动项目列表。
        compact = self.height() < 560
        narrow = self.width() < 900
        self.resize_navigation()
        self.footer_hint.setVisible(not narrow)
        density_changed = self.property('dense') != compact
        self.setProperty('dense', compact)
        self.document_pane.set_compact(compact)
        self.summary.setVisible(not compact)
        self.project_list_heading.setVisible(not compact)
        self.project_page_layout.setContentsMargins(8 if compact else 14, 8 if compact else 14,
                                                   8 if compact else 14, 8 if compact else 12)
        self.project_page_layout.setSpacing(6 if compact else 12)
        self.project_card_layout.setContentsMargins(8 if compact else 10, 6 if compact else 10,
                                                   8 if compact else 10, 6 if compact else 10)
        self.document_card_layout.setContentsMargins(8 if compact else 14, 6 if compact else 12,
                                                    8 if compact else 14, 6 if compact else 10)
        self.document_card_layout.setSpacing(4 if compact else 10)
        self.workbench_layout.setSpacing(4 if compact else 14)
        self.workbench_layout.setContentsMargins(10 if narrow else 24, 8 if compact else 22,
                                               10 if narrow else 24, 8 if compact else 14)
        if density_changed:
            apply_to(self)
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reveal_current_after_resize)

    def _reveal_current_after_resize(self):
        item = self.project_list.currentItem()
        if item and not self.project_list.verticalScrollBar().isSliderDown():
            self.project_list.doItemsLayout()
            # 仅补全当前已在视野中的选中行；选中项在屏外时保留正在阅读的顶行。
            if self.project_list.viewport().rect().intersects(self.project_list.visualItemRect(item)):
                self.project_list.scrollToItem(item, QAbstractItemView.EnsureVisible)


class ProjectDeliveryDialog(QDialog):
    """只确认一个项目及一个明确版本，默认取消，不承载其他项目操作。"""
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle('确认交付 · ' + project['name'])
        self.resize(520, 230)
        layout = QVBoxLayout(self)
        layout.addWidget(label(project['name'], 'title', True))
        caption = label('交付内容')
        layout.addWidget(caption)
        self.version_box = QComboBox()
        self.version_box.setAccessibleName('交付包版本')
        self.version_box.setMinimumContentsLength(16)
        self.version_box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        caption.setBuddy(self.version_box)
        self.version_box.addItem('归档项目工作文件（无需 ZIP）', None)
        for index, version in enumerate(project['zips']):
            text = CompactPanel.project_version_caption(project, version, index)
            if version.get('bundle_state') == 'needs-review':
                text = '待核对 · ' + text
            self.version_box.addItem(text, version['id'])
            facts = CompactPanel.zip_count_details(version)
            self.version_box.setItemData(self.version_box.count()-1,
                version['path'] + '\nSHA256: ' + version['sha256'] + ('\n' + facts if facts else ''), Qt.ToolTipRole)
        layout.addWidget(self.version_box)
        self.explanation = label('', wrap=True)
        layout.addWidget(self.explanation)
        controls = QHBoxLayout()
        controls.addStretch()
        self.cancel_button = button('取消')
        self.confirm_button = button('确认交付', True)
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.confirm)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.confirm_button)
        layout.addLayout(controls)
        self.version_box.currentIndexChanged.connect(self.update_choice)
        self.update_choice()
        self.cancel_button.setFocus(Qt.OtherFocusReason)

    def update_choice(self, *_):
        zid = self.version_box.currentData()
        version = next((version for version in self.project['zips'] if version['id'] == zid), None)
        needs_review = bool(version and version.get('bundle_state') == 'needs-review')
        if needs_review:
            text = '这个 ZIP 的包内项目尚待核对，请先在 ZIP 库完成核对，再确认交付。'
        elif zid is None:
            text = '复制归档项目工作目录中的文件与子目录，保留原件和原文件名；逐项校验成功后设为已交付。首次交付计入当天统计；补交只保留记录。'
        else:
            text = '将所选 ZIP 版本手动设为已交付，不受文件数量判断限制；首次交付计入当天统计，补交只保留记录。可用版本标记独立保留。'
        self.explanation.setText(text)
        self.confirm_button.setEnabled(not needs_review)

    def confirm(self):
        if self.confirm_button.isEnabled():
            self.accept()


class FileIntakeLogDialog(QDialog):
    """普通文件收录的只读事实，不补推旧记录的归属依据。"""

    STATUS_TEXT = {
        'copied': '已收录', 'duplicate': '内容重复', 'failed': '失败',
        'cancelled': '已取消', 'needs-choice': '待选择', 'historical': '旧记录',
    }

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.rows = []
        self.setWindowTitle('普通文件收录记录')
        apply_to(self)
        self.resize(860, 610)
        self.setMinimumSize(640, 460)
        layout = QVBoxLayout(self)
        self.scope_note = label('这里只记录普通文件收录；ZIP 请查看 ZIP 版本记录。旧记录未记录判断依据。', 'subtle', True)
        layout.addWidget(self.scope_note)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName('搜索收录记录')
        self.search.setPlaceholderText('搜索文件名、项目、路径或判断依据')
        self.search.setClearButtonEnabled(True)
        self.refresh_button = button('刷新')
        self.refresh_button.setAutoDefault(False)
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.refresh_button)
        layout.addLayout(search_row)
        self.summary = label('', 'subtle', True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setAccessibleName('普通文件收录记录列表')
        self.table.setHorizontalHeaderLabels(['时间（本地）', '结果', '文件名', '项目'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(130)
        layout.addWidget(self.table, 2)
        layout.addWidget(label('收录详情（可选中文字并复制）', 'subtle'))
        self.details = QPlainTextEdit()
        self.details.setAccessibleName('收录记录只读详情')
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('选择一条记录，查看来源、去向与判断依据。')
        self.details.setMinimumHeight(110)
        layout.addWidget(self.details, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        close = button('关闭')
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.search.textChanged.connect(self.refresh)
        self.refresh_button.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        self.table.setRowCount(0)
        self.details.clear()
        self.rows = []
        try:
            self.rows = self.service.intake_log.list_recent(query=self.search.text(), limit=200)
        except Exception as error:
            self.summary.setText('读取收录记录失败：' + str(error) + '。可点击「刷新」重试。')
            return
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for index, record in enumerate(self.rows):
            values = (local_timestamp(record['created_at']),
                      self.STATUS_TEXT.get(record['status'], record['status']),
                      record['filename'], record['project_name'] or '未指定')
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        if self.rows:
            self.summary.setText(f'显示最近 {len(self.rows)} 条记录，最多 200 条；可搜索更早的记录。')
        else:
            self.summary.setText('没有匹配的收录记录。' if self.search.text().strip() else '还没有普通文件收录记录。')

    def show_details(self):
        index = self.table.currentRow()
        if not self.table.selectedItems() or not 0 <= index < len(self.rows):
            self.details.clear()
            return
        record = self.rows[index]
        historical = record['status'] == 'historical'
        manual = record['manual']
        manual_text = '未记录' if manual is None else ('是' if manual else '否')
        candidates = ', '.join(str(value) for value in record['candidate_ids'])
        candidates = candidates or ('未记录' if historical else '无')
        project = record['project_name'] or '未指定'
        if record['project_id'] is not None:
            project += f"（#{record['project_id']}）"
        self.details.setPlainText('\n'.join((
            '时间（本地）：' + local_timestamp(record['created_at']),
            '结果：' + self.STATUS_TEXT.get(record['status'], record['status']),
            '文件名：' + record['filename'],
            '项目：' + project,
            '来源：' + (record['source_path'] or '未记录'),
            '去向：' + (record['target_path'] or '未记录'),
            '判断依据：' + ('旧记录未记录判断依据' if historical else record['basis'] or '未记录'),
            '人工选择：' + manual_text,
            '候选项目：' + candidates,
            'SHA256：' + (record['sha256'] or '未记录'),
            '说明：' + (record['message'] or '无'),
        )))


class DirectorySettingsDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.types_changed = False
        self.setWindowTitle('工作目录与收录规则设置')
        apply_to(self)
        available = self.screen().availableGeometry()
        self.resize(min(620, available.width() - 32), min(620, available.height() - 32))
        settings = service.workspaces.settings()
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        content.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)
        self.work_root = QLineEdit(settings['work_root'])
        self.archive_root = QLineEdit(settings['archive_root'])
        for title, edit in (('项目工作根目录', self.work_root), ('ZIP 归档根目录', self.archive_root)):
            heading = label(title)
            heading.setBuddy(edit)
            edit.setAccessibleName(title)
            layout.addWidget(heading)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            browse = button('选择目录')
            browse.clicked.connect(lambda _, target=edit: self.browse(target))
            row.addWidget(browse)
            layout.addLayout(row)
        self.auto_create_projects = QCheckBox('无法匹配时自动创建项目')
        self.auto_create_projects.setChecked(settings.get('auto_create_projects', True))
        self.auto_create_projects.setToolTip('仅在名称和目录归属明确时自动创建；有歧义时始终需要选择。关闭后仍可粘贴消息创建项目。')
        layout.addWidget(self.auto_create_projects)
        modes = QHBoxLayout()
        self.manual_mode_button = button('使用通用模式')
        self.manual_mode_button.setAccessibleName('使用通用模式')
        self.manual_mode_button.setObjectName('使用通用模式')
        self.automatic_mode_button = button('使用自动交付模式')
        self.automatic_mode_button.setAccessibleName('使用自动交付模式')
        self.automatic_mode_button.setObjectName('使用自动交付模式')
        modes.addWidget(self.manual_mode_button)
        modes.addWidget(self.automatic_mode_button)
        modes.addStretch()
        layout.addLayout(modes)
        self.auto_deliver_zip = QCheckBox('根据 ZIP 文件数自动确认交付')
        self.auto_deliver_zip.setChecked(settings.get('auto_deliver_zip', True))
        self.manual_mode_button.clicked.connect(lambda: self.auto_deliver_zip.setChecked(False))
        self.automatic_mode_button.clicked.connect(lambda: self.auto_deliver_zip.setChecked(True))
        layout.addWidget(self.auto_deliver_zip)
        layout.addWidget(label('通用模式仍自动归档 ZIP，由你右键确认交付；普通文件人工确认交付时另存归档。自动交付模式按文件数更新状态，数量不代表质量验收。切换仅改变下方自动交付选项，保存后生效。', 'subtle', True))
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(label('每个项目的最少文件数'))
        self.zip_delivery_min_files = QSpinBox()
        self.zip_delivery_min_files.setRange(1, 10000)
        self.zip_delivery_min_files.setSuffix(' 个')
        self.zip_delivery_min_files.setValue(settings.get('zip_delivery_min_files', 4))
        self.zip_delivery_min_files.setAccessibleName('ZIP 自动交付最少文件数')
        self.zip_delivery_min_files.setEnabled(self.auto_deliver_zip.isChecked())
        self.auto_deliver_zip.toggled.connect(self.zip_delivery_min_files.setEnabled)
        threshold_row.addWidget(self.zip_delivery_min_files)
        threshold_row.addStretch()
        layout.addLayout(threshold_row)
        row = QHBoxLayout()
        row.addWidget(label('文件稳定等待'))
        self.stable_seconds = QSpinBox()
        self.stable_seconds.setRange(5, 300)
        self.stable_seconds.setSuffix(' 秒')
        self.stable_seconds.setValue(settings['stable_seconds'])
        self.stable_seconds.setAccessibleName('文件稳定等待秒数')
        row.addWidget(self.stable_seconds)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label('按单个项目统计实际文件，包含子目录，不计文件夹本身。\n规则用于新收录、新版本；已有归档保留原判定和历史记录。\n新目录用于以后创建和归档，已有文件不搬迁。原件保留，退出后暂停监视。', 'subtle', True))
        self.project_types_button = button('管理项目类型…')
        self.project_types_button.setEnabled(hasattr(service, 'list_project_types'))
        self.project_types_button.clicked.connect(self.manage_project_types)
        layout.addWidget(self.project_types_button)
        layout.addWidget(label('项目类型单独保存。新增、改名或停用立即生效，不受本设置窗口的取消影响。', 'subtle', True))
        layout.addStretch()
        self.error = label('', 'subtle', True)
        outer.addWidget(self.error)
        actions = QHBoxLayout()
        self.reset_rules_button = button('恢复默认规则')
        self.reset_rules_button.setToolTip('填回默认规则，点击保存后生效；不改变目录。')
        self.reset_rules_button.clicked.connect(self.reset_rules)
        actions.addWidget(self.reset_rules_button)
        actions.addStretch()
        cancel = button('取消')
        cancel.clicked.connect(self.reject)
        save = button('保存设置', True)
        save.setDefault(True)
        save.clicked.connect(self.save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        outer.addLayout(actions)

    def manage_project_types(self):
        from project_types_ui import ProjectTypesDialog
        with transient_dialog(ProjectTypesDialog(self.service, self)) as dialog:
            dialog.types_changed.connect(self.mark_types_changed)
            dialog.exec()

    def mark_types_changed(self):
        self.types_changed = True

    def browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, '选择本地目录', edit.text())
        if path:
            edit.setText(path)

    def reset_rules(self):
        self.auto_create_projects.setChecked(True)
        self.auto_deliver_zip.setChecked(True)
        self.zip_delivery_min_files.setValue(4)
        self.stable_seconds.setValue(10)

    def save(self):
        try:
            self.service.workspaces.configure(self.work_root.text(), self.archive_root.text(), self.stable_seconds.value(),
                auto_create_projects=self.auto_create_projects.isChecked(),
                auto_deliver_zip=self.auto_deliver_zip.isChecked(),
                zip_delivery_min_files=self.zip_delivery_min_files.value())
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error.setText('设置未保存，请检查后重试：' + str(error))
            return
        self.accept()


class ProjectDetailsDialog(QDialog):
    def __init__(self, service, project, parent=None):
        super().__init__(parent)
        self.service, self.project = service, project
        self.read_only = bool(project.get('archived_at'))
        self.setWindowTitle('项目备注与截止日期')
        apply_to(self)
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        title = label(project['name'], 'title', True)
        layout.addWidget(title)
        if self.read_only:
            layout.addWidget(label('此项目已归档，恢复后可编辑。', 'subtle'))
        notes_label = label('项目备注（最多 10,000 字）')
        self.notes = QPlainTextEdit(project.get('notes', ''))
        self.notes.setAccessibleName('项目备注')
        self.notes.setReadOnly(self.read_only)
        notes_label.setBuddy(self.notes)
        layout.addWidget(notes_label)
        layout.addWidget(self.notes, 1)
        dates = QHBoxLayout()
        self.due_enabled = QCheckBox('设置截止日期')
        self.due_date = QDateEdit()
        self.due_date.setAccessibleName('截止日期')
        self.due_date.setLocale(QLocale(QLocale.Chinese, QLocale.China))
        self.due_date.setDisplayFormat('yyyy-MM-dd')
        self.due_date.setCalendarPopup(True)
        self.due_date.setDateRange(QDate(1, 1, 1), QDate(9999, 12, 31))
        self.due_enabled.setChecked(bool(project.get('due_date')))
        self.due_date.setDate(QDate.fromString(project['due_date'], 'yyyy-MM-dd') if project.get('due_date') else QDate.currentDate())
        self.due_date.setEnabled(self.due_enabled.isChecked() and not self.read_only)
        self.due_enabled.setEnabled(not self.read_only)
        self.due_enabled.toggled.connect(self.due_date.setEnabled)
        dates.addWidget(self.due_enabled)
        dates.addWidget(self.due_date)
        dates.addStretch()
        layout.addLayout(dates)
        layout.addWidget(label('取消勾选可清除日期；日期仅用于项目排序和筛选。', 'subtle', True))
        self.error = label('', 'subtle', True)
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = button('关闭' if self.read_only else '取消')
        cancel.clicked.connect(self.reject)
        self.save_button = button('保存', True)
        self.save_button.setVisible(not self.read_only)
        self.save_button.clicked.connect(self.save)
        actions.addWidget(cancel)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.original = self.values()

    def values(self):
        return self.notes.toPlainText(), self.due_date.date().toString('yyyy-MM-dd') if self.due_enabled.isChecked() else None

    def save(self):
        if self.read_only:
            return
        self.save_button.setEnabled(False)
        try:
            self.service.update_project_details(self.project['id'], *self.values())
        except Exception as error:
            self.error.setText(str(error))
            self.notes.setFocus()
            self.save_button.setEnabled(True)
            return
        self.accept()

    def reject(self):
        if not self.read_only and self.values() != self.original:
            answer = QMessageBox.question(self, '放弃未保存的修改', '备注或日期尚未保存，确定放弃这些修改？', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        super().reject()


class ReviewDialog(QDialog):
    def __init__(self, service, receipt, parent=None):
        super().__init__(parent)
        self.setWindowTitle('核对项目消息')
        apply_to(self)
        self.resize(600, 520)
        self.service, self.receipt = service, receipt
        layout = QVBoxLayout(self)
        layout.addWidget(label(receipt['reason'], 'subtle', True))
        self.raw = QPlainTextEdit(receipt['raw_text'])
        self.raw.setReadOnly(True)
        self.raw.setMaximumHeight(120)
        layout.addWidget(self.raw)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['项目名称（双击修正）', '加急'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)
        for value in receipt['candidates']:
            self.add_row(value['name'], value['urgent'])
        controls = QHBoxLayout()
        add, remove = button('添加一行'), button('移除选中行')
        add.clicked.connect(lambda: self.add_row('', False))
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        controls.addWidget(add)
        controls.addWidget(remove)
        controls.addStretch()
        self.save_button = button('确认收录', True)
        self.save_button.clicked.connect(self.save)
        controls.addWidget(self.save_button)
        layout.addLayout(controls)
        self.error = label('', 'subtle', True)
        layout.addWidget(self.error)

    def add_row(self, name, urgent):
        index = self.table.rowCount()
        self.table.insertRow(index)
        self.table.setItem(index, 0, QTableWidgetItem(name))
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        check.setCheckState(Qt.Checked if urgent else Qt.Unchecked)
        self.table.setItem(index, 1, check)

    def save(self):
        try:
            values = [ParsedProject(self.table.item(i, 0).text(), self.table.item(i, 1).checkState() == Qt.Checked) for i in range(self.table.rowCount())]
            self.service.resolve_text(self.receipt['id'], values)
        except Exception as error:
            self.error.setText(str(error))
            return
        self.accept()
