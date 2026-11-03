"""Pure, conservative material checks adapted from BOX 54; never touch files/data.

Source behaviour and limitations: docs/MATERIAL-CHECKS.md. Suffix families are
labels, not content validation. One stable file identity can satisfy one rule.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
import re
import unicodedata


FAMILIES = {
    'word': ('Word 文档', frozenset('.docx .doc .docm .dotx .dotm .rtf .wps .odt .ott'.split())),
    'pdf': ('PDF', frozenset({'.pdf'})),
    'sheet': ('表格', frozenset('.xlsx .xls .xlsm .xlsb .xltx .xltm .csv .tsv .et .ods .ots'.split())),
    'slides': ('演示文稿', frozenset('.pptx .ppt .pptm .pps .ppsx .ppsm .potx .potm .dps .odp .otp'.split())),
    'text': ('文本', frozenset('.txt .md .log .json .xml .yaml .yml .html .htm .ini .cfg'.split())),
    'image': ('图片', frozenset('.png .jpg .jpeg .gif .bmp .tif .tiff .webp .svg .heic'.split())),
    'video': ('视频', frozenset('.mp4 .wmv .avi .mov .mkv .m4v .webm'.split())),
    'audio': ('音频', frozenset('.mp3 .wav .m4a .aac .flac .ogg .wma'.split())),
    'archive': ('压缩包', frozenset({'.zip', '.7z', '.rar'})),
}
SOFTWARE_TOKEN = '{software}'
_TOKENS = {SOFTWARE_TOKEN, '软件名称', '当前软件名', '{软件名称}'}


@dataclass(frozen=True)
class Requirement:
    id: str
    label: str
    family: str
    count: int = 1
    keywords: str = ''


@dataclass(frozen=True)
class MaterialFile:
    id: str
    name: str


@dataclass(frozen=True)
class MatchRow:
    rule_id: str
    label: str
    required: int
    file_ids: tuple[str, ...]
    matched: int
    missing: int


@dataclass(frozen=True)
class CheckReport:
    rows: tuple[MatchRow, ...]
    ambiguous: dict[str, tuple[str, ...]]
    unmatched: tuple[str, ...]
    status: str
    warnings: tuple[str, ...] = ()
    ignored: int = 0

    @property
    def complete(self) -> bool:
        return self.status == 'complete'


def _text(value: str, label: str, limit: int, *, empty=False) -> str:
    if (not isinstance(value, str) or len(value) > limit
            or (not empty and not value.strip())
            or any(ord(c) < 32 and c not in '\t\n\r' for c in value)):
        raise ValueError(label + '格式无效或超过长度限制。')
    return value


def normalized(value: str) -> str:
    return re.sub(r'[\s_\-—·]+', '', unicodedata.normalize('NFKC', value)).casefold()


def keyword_terms(value: str) -> tuple[str, ...]:
    _text(value, '关键词', 1300, empty=True)
    words = [w for w in re.split(r'[，,+\s]+', unicodedata.normalize('NFKC', value).strip()) if w]
    if len(words) > 12:
        raise ValueError('每项最多12个关键词。')
    result, seen = [], set()
    for word in words:
        word = SOFTWARE_TOKEN if word in _TOKENS else word
        if len(word) > 100 or not normalized(word) or (word != SOFTWARE_TOKEN and ('{' in word or '}' in word)):
            raise ValueError('关键词无效；软件名占位符请使用 {software} 或 软件名称。')
        key = normalized(word)
        if key not in seen:
            result.append(word)
            seen.add(key)
    return tuple(result)


def validate_rules(requirements) -> tuple[Requirement, ...]:
    if not isinstance(requirements, (list, tuple)) or len(requirements) > 40:
        raise ValueError('材料规则必须为清单，最多40项。')
    seen = set()
    for r in requirements:
        if not isinstance(r, Requirement):
            raise ValueError('材料规则记录无效。')
        _text(r.id, '规则编号', 100); _text(r.label, '材料名称', 100)
        _text(r.family, '文件类型', 30)
        if r.id in seen or r.family not in FAMILIES or type(r.count) is not int or not 1 <= r.count <= 20:
            raise ValueError('规则编号重复、类型或份数无效（1至20份）。')
        seen.add(r.id)
        keyword_terms(r.keywords)
    return tuple(requirements)


def match_materials(files, requirements, software_name: str, *, selections=None) -> CheckReport:
    """Report candidates; ambiguous files remain unassigned unless explicitly chosen.

    No greedy allocation or incidental rule order. More uniquely matched files than
    requested remain visible, while counted progress saturates at the requirement.
    """
    rules = sorted(validate_rules(requirements), key=lambda r: r.id)
    _text(software_name, '软件名称', 200, empty=True)
    if not isinstance(files, (list, tuple)) or len(files) > 10000:
        raise ValueError('文件元数据清单无效或超过10000项。')
    unique = {}
    for f in files:
        if not isinstance(f, MaterialFile):
            raise ValueError('文件元数据记录无效。')
        _text(f.id, '文件编号', 200); _text(f.name, '文件名', 1024)
        if '/' in f.name or '\\' in f.name or ':' in f.name:
            raise ValueError('文件名必须是名称，不是目录或文件流。')
        if f.id in unique and unique[f.id] != f:
            raise ValueError('同一文件编号出现不一致的元数据。')
        unique[f.id] = f
    terms = {}
    for r in rules:
        values = keyword_terms(r.keywords)
        if SOFTWARE_TOKEN in values and not normalized(software_name):
            raise ValueError('软件名称为空，无法代入关键词。')
        terms[r.id] = tuple(normalized(software_name if t == SOFTWARE_TOKEN else t) for t in values)
    candidates = {}
    for key, f in sorted(unique.items()):
        # Parse real filename boundaries before search-text normalization.
        # NFKC must not invent an extension or turn a fullwidth slash into a path.
        name = PurePosixPath(f.name)
        suffix = name.suffix.casefold()
        stem = normalized(name.stem)
        candidates[key] = tuple(r.id for r in rules
                                if suffix in FAMILIES[r.family][1] and all(t in stem for t in terms[r.id]))
    choices = {} if selections is None else selections
    if not isinstance(choices, dict) or any(key not in candidates or value not in candidates[key]
                                          for key, value in choices.items()):
        raise ValueError('人工选择已过期或不是有效匹配候选，请重新检查。')
    matched = {r.id: [] for r in rules}
    ambiguous, unmatched = {}, []
    for key, options in candidates.items():
        if key in choices:
            matched[choices[key]].append(key)
        elif len(options) == 1:
            matched[options[0]].append(key)
        elif options:
            ambiguous[key] = options
        else:
            unmatched.append(key)
    rows = tuple(MatchRow(r.id, r.label, r.count, tuple(matched[r.id]),
                          min(r.count, len(matched[r.id])), max(0, r.count - len(matched[r.id]))) for r in rules)
    status = ('unconfigured' if not rules else 'ambiguous' if ambiguous else
              'missing' if any(row.missing for row in rows) else 'complete')
    return CheckReport(rows, ambiguous, tuple(unmatched), status)


def check_snapshot(snapshot: dict, requirements, software_name: str, *, selections=None) -> CheckReport:
    """Adapt existing list_project_documents metadata; archives are not work files."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('documents'), list):
        raise ValueError('文档快照格式无效。')
    errors = snapshot.get('errors', [])
    if not isinstance(errors, list) or any(not isinstance(e, str) for e in errors):
        raise ValueError('文档错误清单无效。')
    warnings = list(errors)
    if not snapshot.get('workspace_path'):
        warnings.append('项目尚无已登记工作目录。')
    files, ignored = [], 0
    for d in snapshot['documents']:
        if not isinstance(d, dict):
            raise ValueError('文档记录无效。')
        if d.get('kind') not in ('工作文件', 'ZIP工作副本') or d.get('exists') is not True or d.get('is_directory') is not False:
            ignored += 1
            continue
        files.append(MaterialFile(d.get('id'), d.get('name')))
    result = match_materials(files, requirements, software_name, selections=selections)
    return replace(result, ignored=ignored, warnings=tuple(dict.fromkeys(warnings)),
                   status='incomplete' if warnings else result.status)


def encode_rules(requirements) -> dict:
    return {'schema': 1, 'rules': [asdict(r) for r in validate_rules(requirements)]}


def decode_rules(payload: dict) -> tuple[Requirement, ...]:
    if (not isinstance(payload, dict) or set(payload) != {'schema', 'rules'}
            or type(payload['schema']) is not int or payload['schema'] != 1
            or not isinstance(payload['rules'], list) or len(payload['rules']) > 40):
        raise ValueError('保存的材料规则版本或格式无效；未覆盖原设置。')
    try:
        rules = tuple(Requirement(**row) for row in payload['rules'])
    except (TypeError, ValueError) as error:
        raise ValueError('保存的材料规则记录无效；未覆盖原设置。') from error
    return validate_rules(rules)


def preset_rules(name: str) -> tuple[Requirement, ...]:
    if name == '说明书与代码':
        items = [('说明书', 'word', '说明书'), ('代码', 'word', '代码')]
    elif name == '五件材料':
        items = [('说明书 Word', 'word', '说明书'), ('说明书 PDF', 'pdf', '说明书'),
                 ('代码 Word', 'word', '代码'), ('代码 PDF', 'pdf', '代码'),
                 ('登记信息', 'text', '登记')]
    elif name == '空白规则':
        items = []
    else:
        raise ValueError('未知的材料规则预设。')
    return tuple(Requirement(f'r{i+1}', label, family, 1, keywords)
                 for i, (label, family, keywords) in enumerate(items))
