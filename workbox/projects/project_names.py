"""Comparison-only project names; never rewrite display names or source paths."""

import re
import unicodedata
from enum import IntEnum


_APP_SPACING = re.compile(r' +(?=APP(?:\s*v?\d+(?:\.\d+)*)?(?:$|_))', re.IGNORECASE)
_DOTTED_VERSION = r'[0-9]+(?:\.[0-9]+)+'
_BARE_VERSION = r'[0-9]{1,3}(?:\.[0-9]+)+'
_VERSION_SUFFIX = re.compile(
    rf'(?P<base>.+?)(?:[ _-]*v\s*{_DOTTED_VERSION}'
    rf'|[ _-]+(?:v\s*[0-9]+|{_BARE_VERSION})'
    rf'|[ _-]*\(\s*(?:v\s*[0-9]+(?:\.[0-9]+)*|{_BARE_VERSION})\s*\))',
    re.IGNORECASE,
)
_LIST_NUMBER = re.compile(r'^(?P<number>[0-9]{1,3})(?:\. |\.(?=[^\W\d_])|[)、] ?)(?P<title>\S.*)$')


class OriginalSiteNameMatch(IntEnum):
    NONE = 0
    OMITTED_VERSION = 1
    OMITTED_NUMBER = 2
    FORMATTED_NUMBER = 3
    FULL = 4


def normalized_project_name(value: str) -> str:
    value = ' '.join(unicodedata.normalize('NFKC', value).split())
    # APP 结尾（含版本或下划线附注）前的空白无身份意义；其他词间空格保持。
    return _APP_SPACING.sub('', value)


def project_name_key(value: str) -> str:
    return normalized_project_name(value).casefold()


def versionless_project_key(title: str) -> str | None:
    """Compare a Word stem to a task name with an explicit trailing version."""
    match = _VERSION_SUFFIX.fullmatch(normalized_project_name(title))
    return (project_name_key(match['base']) or None) if match else None


def numbered_project_key(title: str) -> tuple[str, str] | None:
    """Extract a list number and name for comparison without changing either title."""
    match = _LIST_NUMBER.fullmatch(normalized_project_name(title))
    return (match['number'], project_name_key(match['title'])) if match else None


def numberless_project_key(title: str) -> str | None:
    numbered = numbered_project_key(title)
    return numbered[1] if numbered else None


def _list_number_match(local, other):
    local_numbered = numbered_project_key(local)
    other_numbered = numbered_project_key(other)
    if local_numbered and other_numbered:
        return OriginalSiteNameMatch.FORMATTED_NUMBER if local_numbered == other_numbered else OriginalSiteNameMatch.NONE
    if (local_numbered and local_numbered[1] == other) or (other_numbered and other_numbered[1] == local):
        return OriginalSiteNameMatch.OMITTED_NUMBER
    return OriginalSiteNameMatch.NONE


def original_site_name_match(local_name: str, title: str) -> OriginalSiteNameMatch:
    """Rank full names, omitted list numbers, and omitted source versions."""
    local = project_name_key(local_name)
    full = project_name_key(title)
    if local == full:
        return OriginalSiteNameMatch.FULL
    if number_match := _list_number_match(local, full):
        return number_match
    base = versionless_project_key(full)
    if not base or versionless_project_key(base) is not None or versionless_project_key(local) is not None:
        return OriginalSiteNameMatch.NONE
    if local == base or _list_number_match(local, base):
        return OriginalSiteNameMatch.OMITTED_VERSION
    return OriginalSiteNameMatch.NONE
