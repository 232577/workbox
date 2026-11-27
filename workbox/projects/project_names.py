"""Comparison-only project names; never rewrite display names or source paths."""

import re
import unicodedata


_APP_SPACING = re.compile(r' +(?=APP(?:\s*v?\d+(?:\.\d+)*)?(?:$|_))', re.IGNORECASE)
_DOTTED_VERSION = r'[0-9]+(?:\.[0-9]+)+'
_BARE_VERSION = r'[0-9]{1,3}(?:\.[0-9]+)+'
_VERSION_SUFFIX = re.compile(
    rf'(?P<base>.+?)(?:[ _-]*v\s*{_DOTTED_VERSION}'
    rf'|[ _-]+(?:v\s*[0-9]+|{_BARE_VERSION})'
    rf'|[ _-]*\(\s*(?:v\s*[0-9]+(?:\.[0-9]+)*|{_BARE_VERSION})\s*\))',
    re.IGNORECASE,
)


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


def original_site_binding_name_keys(title: str) -> tuple[str, ...]:
    """Allowed local names for an explicitly selected original-site task."""
    full = project_name_key(title)
    base = versionless_project_key(title)
    if base and versionless_project_key(base) is None:
        return full, base
    return (full,)
