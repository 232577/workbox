"""Compact project lanes for the everyday desktop workflow."""

from collections import Counter

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from workbox.desktop.theme import apply_scrollbar, theme_signal, tokens


_LANES = (
    ('待开发', ('待开发', '开发中')),
    ('待交付', ('待交付',)),
    ('已完成', ('已交付',)),
)


def _label(text, name):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setObjectName(name)
    return label


def _original_batch(project):
    batch_id = project.get('original_batch_id')
    name = str(project.get('original_batch_name') or '').strip()
    return (batch_id, name) if batch_id else (None, '未分批')


def _urgency(project):
    urgency = str(project.get('original_urgency') or '').strip()
    if urgency:
        return '' if urgency in ('普通', '正常', '一般', '默认') else urgency
    return '加急' if project.get('urgent') else ''


class ProjectTitle(QPushButton):
    drag_requested = Signal(int, object)

    def __init__(self, project_id, name, urgency, *, draggable):
        super().__init__()
        self.setObjectName('projectBoardTitle')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.setMinimumHeight(34)
        self.setAccessibleName(name + ('，' + urgency if urgency else ''))
        self.setToolTip(name + ('\n拖动项目名可取出当前文件' if draggable else ''))
        self._project_id = project_id
        self._drag_enabled = draggable
        self._press = None
        row = QHBoxLayout(self)
        row.setContentsMargins(7, 5, 6, 5)
        row.setSpacing(4)
        title = _label(name, 'projectBoardName')
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(title, 1)
        if urgency:
            badge = _label(urgency, 'projectBoardUrgency')
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(badge, 0, Qt.AlignVCenter)

    def mousePressEvent(self, event):
        if self._drag_enabled and event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if (self._press is not None and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self._press).manhattanLength() >= QApplication.startDragDistance()):
            self._press = None
            self.setDown(False)
            self.drag_requested.emit(self._project_id, self)
            return
        super().mouseMoveEvent(event)


class ProjectLane(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName('projectBoardLane')
        self.setAccessibleName(title)
        self.setMinimumWidth(185)
        self.rows = {}
        self.actions = {}
        self._signature = None
        self._reading_anchor = None
        self._resize_anchor = None
        section = QVBoxLayout(self)
        section.setContentsMargins(7, 7, 7, 7)
        section.setSpacing(6)
        heading = QHBoxLayout()
        heading.setContentsMargins(3, 0, 3, 1)
        heading.setSpacing(7)
        heading.addWidget(_label(title, 'projectBoardHeading'))
        self.count = _label('0', 'projectBoardCount')
        heading.addWidget(self.count)
        heading.addStretch()
        section.addLayout(heading)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('projectBoardScroll')
        self.scroll.setAccessibleName(title + '项目')
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        apply_scrollbar(self.scroll.verticalScrollBar())
        self.scroll.verticalScrollBar().valueChanged.connect(self._remember_scroll_anchor)
        section.addWidget(self.scroll, 1)

    def _scroll_anchor(self):
        content = self.scroll.widget()
        position = self.scroll.verticalScrollBar().value()
        first = next(iter(self.rows.values()), None)
        if not position or content is None or first is None or not content.isAncestorOf(first):
            return None
        for pid, row in self.rows.items():
            top = row.mapTo(content, QPoint()).y() - position
            if top + row.height() > 0:
                return pid, top
        return None

    def _remember_scroll_anchor(self, *_):
        self._reading_anchor = self._scroll_anchor()

    def _restore_scroll(self, anchor, fallback):
        if anchor is not None and (row := self.rows.get(anchor[0])) is not None:
            top = max(anchor[1], 1 - row.height())
            fallback = row.mapTo(self.scroll.widget(), QPoint()).y() - top
        self.scroll.verticalScrollBar().setValue(fallback)
        self._remember_scroll_anchor()

    def resizeEvent(self, event):
        if self._resize_anchor is None and event.size().width() != event.oldSize().width():
            # Qt reflows the scroll area's rows before delivering the frame's resize event.
            self._resize_anchor = self._reading_anchor
            if self._resize_anchor is not None:
                QTimer.singleShot(0, self, self._restore_resized_scroll)
        super().resizeEvent(event)

    def _restore_resized_scroll(self):
        self._restore_scroll(self._resize_anchor, self.scroll.verticalScrollBar().value())
        self._resize_anchor = None

    def update_projects(self, projects, duplicates, board, *, group_batches=False):
        entries = []
        for project in projects:
            archived = bool(project.get('archived_at') or project.get('deleted_at'))
            pid = project['id']
            name = project['name']
            if duplicates[name] > 1:
                label = f"原站 #{project['original_task_id']}" if project.get('original_task_id') else f'工作箱 #{pid}'
                name = f'{name} · {label}'
            entries.append((pid, name, _urgency(project), project['status'], archived,
                            _original_batch(project) if group_batches else None))
        signature = tuple(entries)
        if signature == self._signature:
            return
        self._signature = signature
        bar = self.scroll.verticalScrollBar()
        scroll_position = bar.value()
        anchor = self._scroll_anchor()
        focus = QApplication.focusWidget()
        focused_row = next((pid for pid, row in self.rows.items() if row is focus), None)
        focused_action = next((pid for pid, action in self.actions.items() if action is focus), None)
        content = QWidget()
        content.setObjectName('projectBoardContent')
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 1, 1)
        layout.setSpacing(5)
        self.rows.clear()
        self.actions.clear()
        self.count.setText(str(len(projects)))
        self.setAccessibleDescription(f'{len(projects)} 个项目')
        groups = {}
        batch_names = {}
        for entry in entries:
            if group_batches:
                batch_id, batch_name = entry[-1]
                if not batch_names.get(batch_id):
                    batch_names[batch_id] = batch_name
            else:
                batch_id = None
            groups.setdefault(batch_id, []).append(entry)
        for batch_id, group in groups.items():
            target = layout
            if group_batches:
                batch_name = batch_names[batch_id] or f'原站批次 {batch_id}'
                batch_frame = QFrame()
                batch_frame.setObjectName('projectBoardBatch')
                batch_frame.setAccessibleName(batch_name)
                target = QVBoxLayout(batch_frame)
                target.setContentsMargins(5, 4, 5, 5)
                target.setSpacing(4)
                group_title = _label(f'{batch_name}  {len(group)} 项', 'projectBoardBatchTitle')
                group_title.setWordWrap(True)
                group_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                target.addWidget(group_title)
                layout.addWidget(batch_frame)
            for pid, name, urgency, status, archived, _ in group:
                title = ProjectTitle(pid, name, urgency, draggable=status != '待开发' and not archived)
                title.setChecked(pid == board.selected_id)
                title.clicked.connect(lambda _checked=False, value=pid: board.select(value))
                title.drag_requested.connect(board.file_drag_requested)
                self.rows[pid] = title
                if status == '待交付' and not archived:
                    row = QHBoxLayout()
                    row.setSpacing(4)
                    action = QPushButton('交付')
                    action.setObjectName('projectBoardDeliver')
                    action.setAccessibleName('交付 ' + name)
                    action.setCursor(Qt.PointingHandCursor)
                    action.setEnabled(not board.busy)
                    action.clicked.connect(lambda _checked=False, value=pid: board.delivery_requested.emit(value))
                    row.addWidget(title, 1)
                    row.addWidget(action, 0, Qt.AlignVCenter)
                    target.addLayout(row)
                    self.actions[pid] = action
                else:
                    target.addWidget(title)
        if not entries:
            empty = _label('没有项目', 'projectBoardCount')
            empty.setContentsMargins(4, 4, 0, 0)
            layout.addWidget(empty)
        layout.addStretch()
        self.scroll.setWidget(content)
        focused = self.rows.get(focused_row) or self.actions.get(focused_action)
        if focused is not None:
            focused.setFocus(Qt.OtherFocusReason)
        layout.activate()
        self._restore_scroll(anchor, scroll_position)


class ProjectBoard(QWidget):
    project_requested = Signal(int)
    delivery_requested = Signal(int)
    file_drag_requested = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.setAccessibleName('项目看板')
        self.selected_id = None
        self.busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(5)
        columns = QHBoxLayout()
        columns.setSpacing(8)
        outer.addLayout(columns, 1)
        self.lanes = {name: ProjectLane(name) for name, _ in _LANES}
        for lane in self.lanes.values():
            columns.addWidget(lane, 1)
        self._apply_theme()
        theme_signal().connect(self._apply_theme)

    def _apply_theme(self, *_):
        self.setStyleSheet('''
            QFrame#projectBoardLane {
                background: %(subtle_surface)s; border: 1px solid %(border)s; border-radius: 6px;
            }
            QFrame#projectBoardBatch {
                background: %(raised)s; border: 1px solid %(border)s; border-radius: 4px;
            }
            QScrollArea#projectBoardScroll, QWidget#projectBoardContent {
                background: %(subtle_surface)s; border: none;
            }
            QLabel#projectBoardHeading { font-size: 14px; font-weight: 600; }
            QLabel#projectBoardCount { font-size: 12px; color: %(muted)s; }
            QLabel#projectBoardBatchTitle { font-size: 12px; font-weight: 600; color: %(muted)s; }
            QLabel#projectBoardName { font-size: 13px; }
            QLabel#projectBoardUrgency {
                background: %(warning_bg)s; color: %(state_pending)s; font-size: 11px;
                border-radius: 3px; padding: 1px 3px;
            }
            QPushButton#projectBoardTitle {
                background: %(surface)s; border: 1px solid %(border)s; border-radius: 4px; padding: 0;
            }
            QPushButton#projectBoardTitle:hover { background: %(hover)s; }
            QPushButton#projectBoardTitle:checked { background: %(row_selected)s; border-color: %(accent)s; }
            QPushButton#projectBoardTitle:focus, QPushButton#projectBoardDeliver:focus {
                border-color: %(accent)s;
            }
            QPushButton#projectBoardDeliver {
                color: %(accent)s; background: %(surface)s; border: 1px solid %(button_border)s;
                border-radius: 4px; padding: 1px 5px; min-width: 25px; min-height: 24px; font-size: 12px;
            }
            QPushButton#projectBoardDeliver:hover { background: %(notice_bg)s; }
            QPushButton#projectBoardDeliver:disabled { color: %(disabled)s; background: %(disabled_bg)s; }
        ''' % tokens())

    def update_projects(self, projects):
        duplicates = Counter(project['name'] for project in projects)
        for name, states in _LANES:
            rows = [project for project in projects if project['status'] in states]
            self.lanes[name].update_projects(rows, duplicates, self, group_batches=name == '待开发')

    def select(self, project_id):
        self.selected_id = project_id
        for lane in self.lanes.values():
            for row_id, title in lane.rows.items():
                title.setChecked(row_id == project_id)
        self.project_requested.emit(project_id)

    def set_busy(self, busy):
        self.busy = busy
        for action in self.lanes['待交付'].actions.values():
            action.setEnabled(not busy)
