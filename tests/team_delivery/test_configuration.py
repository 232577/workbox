from pathlib import Path

import pytest

from workbox.team_delivery.configuration import load_shared_root, save_shared_root, shared_root, staging_journal


def test_shared_storage_must_exist_and_is_saved_separately(tmp_path):
    settings = tmp_path / 'settings.json'
    missing = tmp_path / 'missing'
    assert load_shared_root(settings) is None
    with pytest.raises(ValueError):
        shared_root(missing)
    assert not missing.exists()

    first, second = tmp_path / '一组', tmp_path / '二组'
    first.mkdir()
    second.mkdir()
    save_shared_root(settings, first)
    assert load_shared_root(settings) == first
    assert staging_journal(tmp_path, first) == staging_journal(tmp_path, Path(str(first)))
    assert staging_journal(tmp_path, first) != staging_journal(tmp_path, second)
    first.rmdir()
    assert load_shared_root(settings) == first
    with pytest.raises(ValueError):
        shared_root(load_shared_root(settings))


def test_corrupt_settings_do_not_select_a_default_directory(tmp_path):
    settings = tmp_path / 'settings.json'
    settings.write_text('{', encoding='utf-8')
    assert load_shared_root(settings) is None
