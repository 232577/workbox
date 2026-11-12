"""Native circular intake surface; no clipboard monitoring or remote fetching."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from branding import draw_mark
from floating_settings import DEFAULT_FLOATING_SIZE, FLOATING_SIZES, validate_floating_size
from theme import tokens, theme_signal
from window_zorder import keep_window_topmost


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def decode_mime(mime):
    unsupported = {'kind': 'unsupported', 'message': '请拖入项目文字、文件或文件夹；支持可访问的共享路径，不接收网址和虚拟附件。'}
    if mime.hasUrls():
        urls = mime.urls()
        # 网络目录检查可能很慢；这里只读拖放数据，实际检查交给后台收件任务。
        if not urls or len(urls) > 100 or any(not u.isLocalFile() for u in urls):
            return unsupported
        return {'kind': 'files', 'paths': [str(Path(u.toLocalFile())) for u in urls]}
    if mime.hasText() and mime.text().strip():
        if len(mime.text()) > 200_000:
            return {'kind': 'unsupported', 'message': '文字太长，请分次拖入（单次最多 200,000 字符）。'}
        return {'kind': 'text', 'text': mime.text()}
    if mime.hasHtml() and len(mime.html()) <= 400_000:
        parser = _TextHTML()
        parser.feed(mime.html())
        text = '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
        if text:
            return {'kind': 'text', 'text': text}
    return unsupported


class DropSurface:
    def dragEnterEvent(self, event):
        data = decode_mime(event.mimeData())
        if data['kind'] != 'unsupported':
            event.setDropAction(Qt.CopyAction)
            event.accept()
            if hasattr(self, 'set_drop_hint'):
                self.set_drop_hint(data['kind'])
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        if hasattr(self, 'set_drop_hint'):
            self.set_drop_hint('')
        event.accept()

    def dropEvent(self, event):
        data = decode_mime(event.mimeData())
        if data['kind'] == 'unsupported':
            event.ignore()
        else:
            # 新收件保留原件，拖放源不能独立删除文件。
            event.setDropAction(Qt.CopyAction)
            event.accept()
        if hasattr(self, 'set_drop_hint'):
            self.set_drop_hint('')
        self.intake.emit(data)


class FloatingBall(DropSurface, QWidget):
    intake = Signal(object)
    open_requested = Signal()
    paste_requested = Signal()
    files_requested = Signal()
    folder_requested = Signal()
    quit_requested = Signal()
    background_requested = Signal()
    position_changed = Signal(object)
    hint_changed = Signal(str)
    size_requested = Signal(int)

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('WorkBox 悬浮球')
        self.setAccessibleName('WorkBox 悬浮球：点击管理，拖文字建项目，拖文件收进项目')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(DEFAULT_FLOATING_SIZE, DEFAULT_FLOATING_SIZE)
        self._hovered = False
        self._press = None
        self._start = QPoint()
        self._dragged = False
        self.drop_kind = ''
        self.count = 0
        self.busy = False
        self.percent = 0
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(1000)
        self._topmost_timer.setTimerType(Qt.CoarseTimer)
        self._topmost_timer.timeout.connect(self._ensure_topmost)
        theme_signal().connect(self._appearance_changed)
        self.setToolTip('文字拖入即收录项目并建工作目录\n文件收进项目，ZIP 稳定后复制归档，原件保留\n单击管理 · 按住拖动 · 右键更多')
        self._update_description()

    def set_orb_size(self, size):
        size = validate_floating_size(size)
        self.setFixedSize(size, size)
        self._update_description()
        self.update()

    def _update_description(self):
        state = f'；正在处理，进度 {self.percent}%' if self.busy else ''
        self.setAccessibleDescription(f'项目数量：{self.count}；悬浮窗尺寸：{self.width()} 像素{state}。按回车打开，右键可调整尺寸。')

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Qt establishes initial topmost order. Wait until its native surface is
        # ready rather than forcing winId() reentrantly during showEvent.
        self._topmost_timer.start()

    def hideEvent(self, event):
        self._topmost_timer.stop()
        super().hideEvent(event)

    def _ensure_topmost(self):
        # Only the orb participates. The expanded manager remains a normal window.
        # Let our menus/dialogs and an in-progress drag finish without Z-order churn.
        if (not self.isVisible() or self.isMinimized() or self._press is not None
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            return
        if QApplication.platformName() == 'windows':
            keep_window_topmost(int(self.winId()))

    def set_drop_hint(self, kind):
        self.drop_kind = kind
        self.hint_changed.emit('松开：收进文件，保留原件' if kind == 'files' else '松开：收录项目并创建工作目录' if kind else '')
        self.update()

    def set_summary(self, count):
        self.count = count
        self._update_description()
        self.update()

    def set_progress(self, busy, percent=0):
        self.busy, self.percent = busy, percent
        self._update_description()
        self.update()

    def _appearance_changed(self, _mode):
        self.update()

    def paintEvent(self, event):
        colors = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Logical geometry scales with the chosen entry size; Qt handles physical DPI.
        p.scale(self.width() / 48, self.height() / 48)
        p.setPen(Qt.NoPen)
        shadow = QColor(colors['orb_shadow'])
        shadow.setAlpha(28)
        p.setBrush(shadow)
        p.drawEllipse(QRectF(2, 3, 44, 44))
        g = QRadialGradient(15, 10, 44)
        g.setColorAt(0, QColor(colors['orb_glow']))
        g.setColorAt(1, QColor(colors['orb_bg']))
        p.setBrush(g)
        active = bool(self.drop_kind or self._hovered or self.hasFocus())
        p.setPen(QPen(QColor(colors['orb_accent'] if active else colors['orb_border']), 1.6 if active else 1))
        p.drawEllipse(QRectF(3, 3, 42, 42))
        p.setBrush(QColor('#10182D'))
        p.setPen(QPen(QColor('#526AA8'), .7))
        p.drawEllipse(QRectF(6, 6, 36, 36))
        draw_mark(p, QRectF(5, 5, 38, 38))
        p.setBrush(Qt.NoBrush)
        if self.drop_kind or self.hasFocus():
            p.setPen(QPen(QColor(colors['orb_accent']), 1.5, Qt.DashLine if self.hasFocus() and not self.drop_kind else Qt.SolidLine))
            p.drawEllipse(QRectF(1.25, 1.25, 45.5, 45.5))
        if self.busy:
            p.setPen(QPen(QColor(colors['orb_accent']), 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(1.75, 1.75, 44.5, 44.5), 90 * 16, -int(max(12, min(100, self.percent) * 3.6) * 16))
        if self.count and not self.busy:
            p.setPen(QPen(QColor(colors['orb_bg']), 1.2))
            p.setBrush(QColor(colors['accent']))
            if self.width() == 32:
                # The compact entry uses a dot instead of illegible tiny digits.
                p.drawEllipse(QRectF(36, 3, 10, 10))
            else:
                badge = QRectF(32, .75, 15, 15)
                p.drawEllipse(badge)
                p.setPen(QColor(colors['accent_text']))
                font = QFont('Segoe UI')
                font.setWeight(QFont.Bold)
                font.setPixelSize(9 if self.count < 100 else 8)
                p.setFont(font)
                p.drawText(badge, Qt.AlignCenter, str(self.count) if self.count < 100 else '99+')
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._start = self.pos()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None:
            delta = event.globalPosition().toPoint() - self._press
            if delta.manhattanLength() >= QApplication.startDragDistance():
                self._dragged = True
            if self._dragged:
                self.move(self._start + delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            if self._dragged:
                self.position_changed.emit([self.x(), self.y()])
            else:
                self.open_requested.emit()
            self._press = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.open_requested.emit()
        elif event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier:
            self.paste_requested.emit()
        else:
            super().keyPressEvent(event)

    def context_menu(self):
        menu = QMenu(self)
        for text, signal in [('打开项目管理', self.open_requested), ('粘贴项目消息', self.paste_requested), ('选择文件收进项目', self.files_requested), ('打开 ZIP 归档目录', self.folder_requested)]:
            menu.addAction(text, signal.emit)
        menu.addSeparator()
        size_menu = menu.addMenu('悬浮窗尺寸')
        group = QActionGroup(size_menu)
        group.setExclusive(True)
        for size in FLOATING_SIZES:
            suffix = '（紧凑）' if size == 32 else '（推荐）' if size == DEFAULT_FLOATING_SIZE else ''
            action = size_menu.addAction(f'{size} × {size}{suffix}')
            action.setCheckable(True)
            action.setData(size)
            action.setChecked(self.width() == size)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=size: self.size_requested.emit(value))
        # The tray holds a long-lived menu while the orb creates transient menus.
        # Reconcile both with the actual persisted-and-applied size on every open.
        size_menu.aboutToShow.connect(lambda: [a.setChecked(a.data() == self.width()) for a in group.actions()])
        menu.addSeparator()
        menu.addAction('后台运行（保留托盘）', self.background_requested.emit)
        menu.addAction('退出 WorkBox', self.quit_requested.emit)
        return menu

    def contextMenuEvent(self, event):
        menu = self.context_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()
