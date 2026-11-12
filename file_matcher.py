"""普通工作文件名的保守归属匹配，不处理 ZIP。"""

from __future__ import annotations

import re
from pathlib import Path

from project_names import normalized_project_name as _normalized

# 先解析语言文字，再做名称比较；C++、C#、C#+Python 不得丢失符号。
_LANGUAGE = r'[A-Za-z][A-Za-z0-9.+#/ -]*'
_CODE_SUFFIX = re.compile(
    rf'(?P<name>.+?)[ _.-]+(?:源代码|代码段|源程序|代码)'
    rf'(?:\s*[（(]{_LANGUAGE}[）)]|[ _-]+{_LANGUAGE})?$',
    re.IGNORECASE,
)
_DOCUMENT_SUFFIX = re.compile(
    r'(?P<name>.+?)[ _.-]+(?:操作手册|用户手册|申请书|申请表)'
    r'(?:[ _.-]*(?:重新下载|下载说明|下载链接))?$'
)
_DOWNLOAD_COPY = re.compile(r'(?P<name>.+) \([1-9]\d*\)$')
_RELEASE_SUFFIX = re.compile(
    r'(?P<name>.+?)[ _.-]+(?:v?\d+\.\d+(?:\.\d+)*|20\d{6}|'
    r'20\d{2}[-.]\d{2}[-.]\d{2}|final|release|delivery|'
    r'交付包|交付|成品|完成版|正式版|最终版)$',
    re.IGNORECASE,
)
_EDITION_SUFFIX = re.compile(r'(?P<name>.+?)[ _.-]+[^ _./\\-]{1,20}版$')


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


def match_annotated_project_names(stem: str, projects: list[dict]) -> dict:
    """已有完整名后的下划线可接任意附注；只保留最长且同强度的归属。"""
    key = _normalized(stem).casefold()
    best = (0, False)
    candidates, seen, recognized = [], set(), None
    for project in projects:
        names = [(project['name'], True),
                 *((alias, False) for alias in project.get('name_aliases', []) if isinstance(alias, str))]
        for name, current in names:
            normalized = _normalized(name)
            prefix = normalized.casefold()
            if not prefix or not key.startswith(prefix + '_'):
                continue
            if not key[len(prefix) + 1:].strip(' _'):
                continue
            strength = (len(prefix), current)
            if strength > best:
                best, candidates, seen, recognized = strength, [], set(), normalized
            if strength == best and project['id'] not in seen:
                candidates.append(project)
                seen.add(project['id'])
    return dict(candidates=candidates, recognized_name=recognized,
                basis='完整项目名与下划线附注匹配' if candidates else '未识别文件名')


def match_work_file_projects(filename: str, projects: list[dict], *, include_annotations=True) -> dict:
    """完整名称、明确尾缀优先，再匹配已有名称后的任意下划线附注。"""
    source = Path(filename)
    if source.suffix.casefold() == '.zip':
        return {'candidates': [], 'basis': 'ZIP 使用专门匹配规则', 'recognized_name': None}

    stem = _normalized(source.stem)
    basis = []
    variants = []
    while stem:
        variants.append((stem, list(basis)))
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
            (_EDITION_SUFFIX, '版本名称后缀'),
        ):
            match = rule.fullmatch(stem)
            if match:
                stem = _normalized(match.group('name'))
                basis.append(reason)
                break
        else:
            break
    # 当前完整名称及逐层尾缀规则均无结果后，才使用明确登记的历史名称。
    for stem, reasons in variants:
        candidates = [project for project in projects
                      if any(isinstance(alias, str) and _normalized(alias).casefold() == stem.casefold()
                             for alias in project.get('name_aliases', []))]
        if candidates:
            reason = '、'.join([*reasons, '已登记旧项目名']) + '匹配'
            return {'candidates': candidates, 'basis': reason, 'recognized_name': stem}
    if include_annotations:
        return match_annotated_project_names(source.stem, projects)
    return {'candidates': [], 'basis': '未识别文件名', 'recognized_name': None}
