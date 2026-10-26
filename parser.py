"""Deterministic, offline project-title parsing with an editable UI preview."""

import re
import unicodedata
from dataclasses import dataclass, replace

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
TITLE_END = re.compile(r"(?:系统|平台|工具|软件|应用|小程序|网站|助手|工作箱)(?:\s*v?\d+(?:\.\d+)*)?$", re.IGNORECASE)


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
    lines = [line.strip() for line in re.split(r"[\r\n；;]+", text.lstrip("\ufeff")) if line.strip()]
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
        key = unicodedata.normalize("NFKC", " ".join(project.name.split())).casefold()
        if key in unique:
            unique[key] = replace(unique[key], urgent=unique[key].urgent or project.urgent)
        else:
            unique[key] = project
    return list(unique.values())
