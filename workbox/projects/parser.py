"""Deterministic, offline project-title parsing with an editable UI preview."""

import re
from dataclasses import dataclass, replace

from workbox.projects.project_names import project_name_key

MAX_MESSAGE_LENGTH = 200_000
MAX_PROJECT_NAME = 200
NUMBERED = re.compile(r"^\s*(?:\d{1,3}[.)、．]|[（(][\d一二三四五六七八九十]+[)）]|[一二三四五六七八九十]+[、.]|[-*•●▪])\s*(.+)$")
PROJECT_LABEL = re.compile(r"^(?:项目名称|项目名|项目|名称)\s*[:：]\s*(.+)$")
NOTES = re.compile(r"^(?:说明|备注|需求|功能|要求|截止|时间|联系人|交付时间)\s*[:：]")
TITLE = re.compile(r"《([^《》\n]+)》")
URGENCY = re.compile(
    r"[\[（(【]\s*(?:加急|urgent)\s*[\]）)】]|^(?:加急|urgent)\s*[:：|｜-]\s*|(?<!\S)(?:加急|urgent)(?!\S)",
    re.IGNORECASE,
)
TITLE_END = re.compile(r"(?:系统|平台|工具|软件|应用|小程序|网站|助手|工作箱|APP)(?:\s*v?\d+(?:\.\d+)*)?$", re.IGNORECASE)
_INLINE_NUMBER = re.compile(r"(?<![\dA-Za-z_.．])(\d{1,3})[.)、．](?=\s*(?:[^\W\d_]|《))")


def split_project_lines(text: str) -> tuple[list[str], bool]:
    lines = []
    ambiguous = False
    for value in re.split(r"[\r\n；;]+", text.lstrip("\ufeff")):
        line = value.strip()
        if not line:
            continue
        markers = list(_INLINE_NUMBER.finditer(line)) if _INLINE_NUMBER.match(line) else []
        if len(markers) < 2:
            lines.append(line)
            continue
        numbers = [int(marker[1]) for marker in markers]
        ends = [marker.start() for marker in markers[1:]] + [len(line)]
        names = [URGENCY.sub("", line[marker.end():end]).strip(" \t《》,，。")
                 for marker, end in zip(markers, ends, strict=True)]
        # Consecutive numbering and recognizable project title endings distinguish
        # pasted list entries from a number or description inside one title.
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))) or not all(
                TITLE_END.search(name) for name in names):
            lines.append(line)
            ambiguous = True
            continue
        lines.extend(line[marker.start():end].strip(" \t,，。")
                     for marker, end in zip(markers, ends, strict=True))
    return lines, ambiguous


def _project(value: str, urgent: bool = False) -> "ParsedProject | None":
    urgent = urgent or bool(URGENCY.search(value))
    name = URGENCY.sub("", value).strip(" \t《》,，。；;|｜")
    if not name:
        return None
    if len(name) > MAX_PROJECT_NAME:
        raise ValueError("项目名称不能超过 200 个字符，请在预览中手工调整。")
    return ParsedProject(name, urgent)


@dataclass(frozen=True)
class ParsedProject:
    name: str
    urgent: bool = False


def parse_message(text: str) -> list[ParsedProject]:
    if not isinstance(text, str):
        raise ValueError("项目消息必须是文本。")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValueError("单条消息不能超过 200,000 个字符。")
    lines, ambiguous = split_project_lines(text)
    if ambiguous:
        raise ValueError("同一行有多个编号，请核对项目名并将每个项目分成一行。")
    structured: list[ParsedProject] = []
    plain: list[str] = []
    for line in lines:
        if NOTES.match(line) or line.endswith(("：", ":")):
            continue
        titles = list(TITLE.finditer(line))
        if titles:
            for index, match in enumerate(titles):
                end = titles[index + 1].start() if index + 1 < len(titles) else len(line)
                suffix = re.split(r"[,，。]", line[match.end():end], maxsplit=1)[0]
                prefix = line[:match.start()] if index == 0 else ""
                value = _project(match.group(1), bool(URGENCY.search(prefix + suffix)))
                if value:
                    structured.append(value)
            continue
        match = NUMBERED.match(line) or PROJECT_LABEL.match(line)
        if match:
            value = _project(match.group(1))
            if value:
                structured.append(value)
        else:
            plain.append(line)
    if not structured and plain:
        # Multiple short titles are a list; prose remains one editable candidate.
        explicit_separator = bool(re.search(r"[；;]", text))
        values = plain if explicit_separator or len(plain) == 1 or all(TITLE_END.search(s) for s in plain) else plain[:1]
        structured = [value for s in values if (value := _project(s)) is not None]
    unique: dict[str, ParsedProject] = {}
    for project in structured:
        key = project_name_key(project.name)
        if key in unique:
            unique[key] = replace(unique[key], urgent=unique[key].urgent or project.urgent)
        else:
            unique[key] = project
    return list(unique.values())
