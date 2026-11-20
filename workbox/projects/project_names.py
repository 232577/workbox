"""Comparison-only project names; never rewrite display names or source paths."""

import re
import unicodedata


_APP_SPACING = re.compile(r' +(?=APP(?:\s*v?\d+(?:\.\d+)*)?(?:$|_))', re.IGNORECASE)


def normalized_project_name(value: str) -> str:
    value = ' '.join(unicodedata.normalize('NFKC', value).split())
    # APP 结尾（含版本或下划线附注）前的空白无身份意义；其他词间空格保持。
    return _APP_SPACING.sub('', value)


def project_name_key(value: str) -> str:
    return normalized_project_name(value).casefold()
