"""Resolve the urgency shown in the project board."""


def board_urgency(project):
    source_urgency = str(project.get('original_urgency') or '').strip()
    if source_urgency:
        return '' if source_urgency in ('普通', '正常', '一般', '默认') else source_urgency
    return '加急' if project.get('urgent') else ''
