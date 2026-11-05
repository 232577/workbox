"""共用Excel保存入口：最终名称确认与文件身份传递。"""
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QMessageBox
from database import DELIVERY_TIMEZONE
from native_files import identity


def choose_workbook_destination(parent, prefix='WorkBox交付项目'):
    documents = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', prefix)
    filename = prefix + '_' + datetime.now(DELIVERY_TIMEZONE).strftime('%Y%m%d_%H%M%S') + '.xlsx'
    selected, _ = QFileDialog.getSaveFileName(
        parent, '导出工作箱表格', str(Path(documents or str(Path.home())) / filename),
        'Excel 工作簿 (*.xlsx)', options=QFileDialog.DontConfirmOverwrite)
    if not selected:
        return None
    target = Path(selected)
    if not target.suffix:
        target = target.with_suffix('.xlsx')
    if target.suffix.casefold() != '.xlsx':
        raise ValueError('请选择 .xlsx 文件。')
    try:
        previous = identity(target.stat())
    except FileNotFoundError:
        previous = None
    if previous is not None and QMessageBox.question(
            parent, '替换文件', f'文件已存在，是否替换？\n\n{target}',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
        return None
    return target, previous
