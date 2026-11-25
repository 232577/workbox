import hashlib

import pytest

from scripts import stage_webengine_runtime as runtime


@pytest.fixture
def layout(tmp_path, monkeypatch):
    frozen = tmp_path / 'dist' / 'WorkBox'
    qt_dist = frozen / '_internal' / 'PySide6'
    qt_wheel = tmp_path / 'venv' / 'PySide6'
    qt_wheel.mkdir(parents=True)
    dlls = {}
    for name in runtime.MSVC_RUNTIME:
        data = name.encode()
        (qt_wheel / name).write_bytes(data)
        dlls[name] = (len(data), hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(runtime, 'MSVC_RUNTIME', dlls)
    for name in runtime.WEBENGINE_FILES:
        file = qt_dist / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'Qt fixture')
    designer = qt_dist / runtime.DESIGNER_METADATA
    designer.parent.mkdir(parents=True)
    designer.write_bytes(b'designer fixture')
    monkeypatch.setattr(runtime, 'DESIGNER_IDENTITY',
                        (designer.stat().st_size, hashlib.sha256(designer.read_bytes()).hexdigest()))
    return frozen, qt_dist, qt_wheel, designer


def test_stages_verified_app_local_runtime_and_installer_excludes_designer_metadata(layout):
    frozen, qt_dist, qt_wheel, designer = layout
    manifest = runtime.stage_webengine_runtime(frozen, qt_wheel)
    assert set(manifest) == set(runtime.WEBENGINE_FILES) | runtime.MSVC_RUNTIME.keys()
    for name in runtime.MSVC_RUNTIME:
        assert (qt_dist / name).read_bytes() == (qt_wheel / name).read_bytes()
    assert not designer.exists()
    assert runtime.stage_webengine_runtime(frozen, qt_wheel) == manifest


@pytest.mark.parametrize('unsafe', ['missing_locale', 'unexpected_dll', 'unverified_wheel', 'unverified_designer'])
def test_aborts_before_mutation_on_unverified_or_incomplete_inputs(layout, unsafe):
    frozen, qt_dist, qt_wheel, designer = layout
    first, last = tuple(runtime.MSVC_RUNTIME)[::2]
    if unsafe == 'missing_locale':
        (qt_dist / 'translations/qtwebengine_locales/zh-CN.pak').unlink()
    elif unsafe == 'unexpected_dll':
        (qt_dist / last).write_bytes(b'unexpected DLL')
    elif unsafe == 'unverified_wheel':
        (qt_wheel / last).write_bytes(b'changed wheel')
    else:
        designer.write_bytes(b'unexpected JSON')
    with pytest.raises(ValueError):
        runtime.stage_webengine_runtime(frozen, qt_wheel)
    assert not (qt_dist / first).exists()
    assert designer.exists()
