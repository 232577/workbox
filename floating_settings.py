"""Local floating-entry geometry; deliberately independent of Qt and workflow policy."""

DEFAULT_FLOATING_SIZE = 48
FLOATING_SIZES = (32, 40, 48, 56, 64)


def validate_floating_size(value: int) -> int:
    if type(value) is not int or value not in FLOATING_SIZES:
        raise ValueError('悬浮窗尺寸只支持 32、40、48、56 或 64 像素。')
    return value
