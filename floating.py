"""Native circular intake surface; no clipboard monitoring or remote fetching."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget


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
    unsupported = {'kind': 'unsupported', 'message': '请拖入项目文字或已下载的本地文件；不接收网址、文件夹和虚拟附件。'}
    if mime.hasUrls():
        urls = mime.urls()
        if not urls or len(urls) > 100 or any(not u.isLocalFile() or Path(u.toLocalFile()).is_dir() for u in urls):
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

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('WorkBox 悬浮球')
        self.setAccessibleName('WorkBox 悬浮球：点击管理，拖文字建项目，拖文件收进项目')
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(76, 76)
        self._press = None
        self._start = QPoint()
        self._dragged = False
        self.drop_kind = ''
        self.count = 0
        self.busy = False
        self.percent = 0
        self.setToolTip('文字拖入即收录项目并建工作目录\n文件收进项目，ZIP 稳定后复制归档，原件保留\n单击管理 · 按住拖动 · 右键更多')

    def set_drop_hint(self, kind):
        self.drop_kind = kind
        self.hint_changed.emit('松开：收进文件，保留原件' if kind == 'files' else '松开：收录项目并创建工作目录' if kind else '')
        self.update()

    def set_summary(self, count):
        self.count = count
        self.update()

    def set_progress(self, busy, percent=0):
        self.busy, self.percent = busy, percent
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(27, 50, 64, 28))
        p.drawEllipse(QRectF(4, 6, 68, 68))
        g = QRadialGradient(27, 20, 66)
        g.setColorAt(0, QColor('#5188A0'))
        g.setColorAt(1, QColor('#203E55'))
        p.setBrush(g)
        p.setPen(QPen(QColor('#A6C8D8'), 1.2))
        p.drawEllipse(QRectF(5, 4, 66, 66))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor('#FFFFFF' if self.drop_kind else '#6F9DB2'), 2 if self.drop_kind else 1))
        p.drawEllipse(QRectF(10, 9, 56, 56))
        p.setPen(QPen(QColor('white'), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        tray = QPainterPath()
        tray.moveTo(23, 40)
        tray.lineTo(23, 49)
        tray.lineTo(53, 49)
        tray.lineTo(53, 40)
        p.drawPath(tray)
        p.drawLine(38, 24, 38, 40)
        p.drawLine(32, 34, 38, 40)
        p.drawLine(38, 40, 44, 34)
        if self.busy:
            p.setPen(QPen(QColor('#A9DBED'), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(3, 2, 70, 70), 90 * 16, -int(max(12, self.percent * 3.6) * 16))
        if self.count and not self.busy:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#EAF3F7'))
            p.drawEllipse(QRectF(51, 1, 22, 22))
            p.setPen(QColor('#203E55'))
            p.setFont(QFont('Segoe UI', 9, QFont.Bold))
            p.drawText(QRectF(51, 1, 22, 22), Qt.AlignCenter, str(self.count) if self.count < 100 else '99+')
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
        menu.addAction('后台运行（保留托盘）', self.background_requested.emit)
        menu.addAction('退出 WorkBox', self.quit_requested.emit)
        return menu

    def contextMenuEvent(self, event):
        menu = self.context_menu()
        menu.exec(event.globalPos())
        menu.deleteLater()
