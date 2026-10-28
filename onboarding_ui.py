"""首次设置与只读使用指南，沿用工作设置的保存与目录校验。"""

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLineEdit, QRadioButton, QScrollArea,
    QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from compact_ui import STYLE, button, label
from ui import app_icon


def _identify(widget, name):
    if not widget.objectName():
        widget.setObjectName(name)
    widget.setAccessibleName(name)
    return widget


def _dialog_layout(dialog, title):
    dialog.setWindowTitle(title)
    dialog.setWindowIcon(app_icon())
    dialog.setStyleSheet(STYLE)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(8)
    return layout


def _fit_screen(dialog, width, height):
    available = dialog.screen().availableGeometry()
    dialog.resize(min(width, max(280, available.width() - 32)),
                  min(height, max(240, available.height() - 32)))


def _scroll_content(content):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return scroll


def _instructions(layout):
    for title, text in (
        ('1. 收进项目', '点击“粘贴消息”或将项目文字拖到悬浮球，核对后创建项目和工作目录。'),
        ('2. 放入文件', '将文件或文件夹拖到悬浮球，也可点击“收进文件”。名称明确时自动匹配；有歧义时选择对应项目。原件保留。'),
        ('3. 归档与交付', '已登记工作目录中的 ZIP 和主动收进的 ZIP 会持续监视，ZIP 稳定后复制归档。通用模式请在项目右键菜单点击“确认交付…”。普通文件收进工作目录，人工确认交付时才另存归档，无需 ZIP。'),
    ):
        layout.addWidget(label(title, 'title', True))
        layout.addWidget(label(text, '', True))


class FirstRunDialog(QDialog):
    """只有最后点击完成并成功保存，才完成首次设置。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._initial_settings = service.workspaces.settings()
        settings = self._initial_settings
        layout = _dialog_layout(self, '欢迎使用 WorkBox · 首次设置')
        self.step_label = _identify(label('', 'title', True), '首次设置步骤')
        layout.addWidget(self.step_label)
        self.pages = _identify(QStackedWidget(), '首次设置内容')
        self.scroll = _scroll_content(self.pages)
        layout.addWidget(self.scroll, 1)

        directories = QWidget()
        directory_layout = QVBoxLayout(directories)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.addWidget(label('选择两个独立的本地目录。可以直接使用下方位置，也可稍后在“工作设置”更改。', '', True))
        self.work_root = _identify(QLineEdit(settings['work_root']), '项目工作根目录')
        self.archive_root = _identify(QLineEdit(settings['archive_root']), 'ZIP 归档根目录')
        for title, edit, attribute in (
            ('项目工作根目录', self.work_root, 'work_browse_button'),
            ('ZIP 归档根目录', self.archive_root, 'archive_browse_button'),
        ):
            heading = label(title)
            heading.setBuddy(edit)
            directory_layout.addWidget(heading)
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            browse = _identify(button('选择目录'), '选择' + title)
            browse.clicked.connect(lambda _, target=edit: self.browse(target))
            setattr(self, attribute, browse)
            row.addWidget(browse)
            directory_layout.addLayout(row)
        directory_layout.addWidget(label('目录不能相同或互相包含，不接受网络或映射共享盘作为目标。已有项目、文件和历史记录会保留。', 'subtle', True))
        directory_layout.addStretch()
        self.pages.addWidget(directories)

        modes = QWidget()
        mode_layout = QVBoxLayout(modes)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_mode = _identify(QRadioButton('通用模式（推荐）'), '通用模式（推荐）')
        self.automatic_mode = _identify(QRadioButton('自动交付模式'), '自动交付模式')
        mode_layout.addWidget(self.manual_mode)
        mode_layout.addWidget(label('普通文件收进工作目录，ZIP 稳定后自动复制归档。项目保持待交付，由你核对后在右键菜单确认交付。适合日常项目管理。', '', True))
        mode_layout.addWidget(self.automatic_mode)
        mode_layout.addWidget(label('按每个项目实际文件数自动设为已交付。适合已约定好交付文件数量的流程。', '', True))
        threshold = QHBoxLayout()
        heading = label('每个项目至少')
        self.zip_delivery_min_files = _identify(QSpinBox(), 'ZIP 自动交付最少文件数')
        self.zip_delivery_min_files.setRange(1, 10000)
        self.zip_delivery_min_files.setSuffix(' 个文件')
        self.zip_delivery_min_files.setValue(settings.get('zip_delivery_min_files', 4))
        heading.setBuddy(self.zip_delivery_min_files)
        threshold.addWidget(heading)
        threshold.addWidget(self.zip_delivery_min_files)
        threshold.addStretch()
        mode_layout.addLayout(threshold)
        self.automatic_mode.toggled.connect(self.zip_delivery_min_files.setEnabled)
        self.manual_mode.setChecked(True)
        self.zip_delivery_min_files.setEnabled(False)
        mode_layout.addWidget(label('文件数包含项目子目录中的实际文件，不计文件夹。数量门槛不代表质量验收；待核对的 ZIP 仍需处理。两种模式都可在“工作设置”切换。', 'subtle', True))
        mode_layout.addStretch()
        self.pages.addWidget(modes)

        next_steps = QWidget()
        next_layout = QVBoxLayout(next_steps)
        next_layout.setContentsMargins(0, 0, 0, 0)
        _instructions(next_layout)
        self.summary = _identify(label('', 'notice', True), '首次设置确认摘要')
        next_layout.addWidget(self.summary)
        next_layout.addWidget(label('点击“完成设置”后保存并进入悬浮球。点击取消或关闭窗口，不保存本次设置。后台运行保留归档监视；退出后暂停。', 'subtle', True))
        next_layout.addStretch()
        self.pages.addWidget(next_steps)

        self.error = _identify(label('', 'subtle', True), '首次设置错误提示')
        self.error.hide()
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        self.cancel_button = _identify(button('取消'), '取消首次设置')
        self.back_button = _identify(button('上一步'), '首次设置上一步')
        self.next_button = _identify(button('下一步', True), '首次设置下一步')
        self.finish_button = _identify(button('完成设置', True), '完成首次设置')
        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(lambda: self.set_step(self.pages.currentIndex() - 1))
        self.next_button.clicked.connect(lambda: self.set_step(self.pages.currentIndex() + 1))
        self.finish_button.clicked.connect(self.save)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        actions.addWidget(self.back_button)
        actions.addWidget(self.next_button)
        actions.addWidget(self.finish_button)
        layout.addLayout(actions)
        self.set_step(0)
        _fit_screen(self, 580, 510)

    def browse(self, edit):
        path = QFileDialog.getExistingDirectory(self, '选择本地目录', edit.text())
        if path:
            edit.setText(path)

    def set_step(self, index):
        if not 0 <= index < self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        self.step_label.setText(('第 1 步，共 3 步 · 选择保存位置', '第 2 步，共 3 步 · 选择工作模式', '第 3 步，共 3 步 · 开始使用')[index])
        self.back_button.setEnabled(index > 0)
        self.next_button.setVisible(index < 2)
        self.next_button.setEnabled(index < 2)
        self.next_button.setDefault(index < 2)
        self.finish_button.setVisible(index == 2)
        self.finish_button.setEnabled(index == 2)
        self.finish_button.setDefault(index == 2)
        if index == 2:
            mode = '自动交付模式' if self.automatic_mode.isChecked() else '通用模式：归档后由你确认交付'
            self.summary.setText('将保存：' + mode + '。完成后可随时打开“使用指南”查看当前设置。')
        self.scroll.verticalScrollBar().setValue(0)

    def save(self):
        if self.pages.currentIndex() != 2:
            return
        try:
            self.service.workspaces.configure(
                self.work_root.text(), self.archive_root.text(), self._initial_settings['stable_seconds'],
                auto_create_projects=self._initial_settings.get('auto_create_projects', True),
                auto_deliver_zip=self.automatic_mode.isChecked(),
                zip_delivery_min_files=self.zip_delivery_min_files.value(), complete_setup=True)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error.setText('设置未保存：' + str(error) + '\n请返回检查目录，或解除文件占用后点击“完成设置”重试。')
            self.error.show()
            return
        self.accept()


class QuickStartDialog(QDialog):
    """随时查看当前规则和操作，不提供任何设置写入。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        layout = _dialog_layout(self, 'WorkBox · 使用指南')
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        settings = service.workspaces.settings()
        minimum = settings.get('zip_delivery_min_files', 4)
        mode = (f'当前：自动交付模式 · 每项目至少 {minimum} 个文件自动设为已交付。'
                if settings.get('auto_deliver_zip', True) else '当前：通用模式 · ZIP 自动归档后，由你人工确认交付。')
        self.mode_label = _identify(label(mode, 'notice', True), '当前工作模式')
        body.addWidget(self.mode_label)
        self.auto_create_label = _identify(label('自动创建项目：' + ('开启，名称明确时可自动创建。' if settings.get('auto_create_projects', True)
            else '关闭，请先粘贴项目文字创建项目，再收进文件。'), '', True), '当前自动建项规则')
        body.addWidget(self.auto_create_label)
        self.work_root = _identify(QLineEdit(settings['work_root']), '当前项目工作根目录')
        self.archive_root = _identify(QLineEdit(settings['archive_root']), '当前 ZIP 归档根目录')
        for title, edit in (('项目工作根目录', self.work_root), ('ZIP 归档根目录', self.archive_root)):
            edit.setReadOnly(True)
            heading = label(title)
            heading.setBuddy(edit)
            body.addWidget(heading)
            body.addWidget(edit)
        _instructions(body)
        body.addWidget(label(f'文件稳定等待：{settings["stable_seconds"]} 秒。数量门槛不代表质量验收。“标记可用版本”和“确认交付”分别操作。切换模式或目录请打开“工作设置”，只影响后续新收录与新版本。', 'subtle', True))
        body.addWidget(label('后台运行保留托盘和归档监视；关闭管理面板仍有悬浮球，退出后暂停监视。此指南只读，关闭不会更改设置。', 'subtle', True))
        body.addStretch()
        layout.addWidget(_scroll_content(content), 1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.close_button = _identify(button('关闭指南'), '关闭使用指南')
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        _fit_screen(self, 580, 570)
