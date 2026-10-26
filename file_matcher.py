"""普通工作文件名的保守归属匹配，不处理 ZIP。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


# 先解析语言文字，再做名称比较；C++、C#、C#+Python 不得丢失符号。
_LANGUAGE = r'[A-Za-z][A-Za-z0-9.+#/ -]*'
_CODE_SUFFIX = re.compile(
    rf'(?P<name>.+?)[ _.-]+(?:源代码|代码段|源程序|代码)'
    rf'(?:\s*[（(]{_LANGUAGE}[）)]|[ _-]+{_LANGUAGE})?$',
    re.IGNORECASE,
)
_DOCUMENT_SUFFIX = re.compile(r'(?P<name>.+?)[ _.-]+(?:操作手册|用户手册|申请书|申请表)$')
_DOWNLOAD_COPY = re.compile(r'(?P<name>.+) \([1-9]\d*\)$')
_RELEASE_SUFFIX = re.compile(
    r'(?P<name>.+?)[ _.-]+(?:v?\d+\.\d+(?:\.\d+)*|20\d{6}|'
    r'20\d{2}[-.]\d{2}[-.]\d{2}|final|release|delivery|'
    r'交付包|交付|成品|完成版|正式版|最终版)$',
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    # 保留词间空格及全部标点，只统一全半角、空白和大小写。
    return ' '.join(unicodedata.normalize('NFKC', value).split())


def new_file_project_name(filename: str) -> str:
    """仅剥去明确的材料尾缀，保留项目名原有标点和全半角。"""
    name = Path(filename).stem.strip()
    while name:
        for rule in (_CODE_SUFFIX, _DOCUMENT_SUFFIX, _DOWNLOAD_COPY, _RELEASE_SUFFIX):
            match = rule.fullmatch(name)
            if match:
                name = match.group('name').strip()
                break
        else:
            break
    if not name or len(name) > 200:
        raise ValueError('项目名称需要 1 至 200 个字符，请调整文件名。')
    return name


def match_work_file_projects(filename: str, projects: list[dict]) -> dict:
    """只返回完整名称相同的全部项目，不选择批次、不改变业务状态。"""
    source = Path(filename)
    if source.suffix.casefold() == '.zip':
        return {'candidates': [], 'basis': 'ZIP 使用专门匹配规则', 'recognized_name': None}

    stem = _normalized(source.stem)
    basis = []
    while stem:
        candidates = [project for project in projects
                      if _normalized(project['name']).casefold() == stem.casefold()]
        if candidates:
            reason = '、'.join(basis) + '匹配' if basis else '文件名完整匹配'
            return {'candidates': candidates, 'basis': reason, 'recognized_name': stem}

        # 每剥去一个明确尾缀便重新检查完整名，避免剥掉项目自己的版本或语言。
        for rule, reason in (
            (_CODE_SUFFIX, '代码文件后缀'),
            (_DOCUMENT_SUFFIX, '材料文件后缀'),
            (_DOWNLOAD_COPY, '下载副本'),
            (_RELEASE_SUFFIX, '版本或交付后缀'),
        ):
            match = rule.fullmatch(stem)
            if match:
                stem = match.group('name').strip()
                basis.append(reason)
                break
        else:
            break
    return {'candidates': [], 'basis': '未识别文件名', 'recognized_name': None}
