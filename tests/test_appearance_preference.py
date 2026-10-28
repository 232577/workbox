import pytest
from desktop_service import DesktopService


@pytest.mark.parametrize('mode', ['light', 'dark', 'system'])
def test_appearance_is_independent_and_persists_without_changing_workflow(tmp_path, mode):
    service = DesktopService(tmp_path / 'data/workbox.db')
    settings = service.workspaces.settings()
    pending = service.workspaces.setup_required()
    service.set_setting('appearance_theme', mode)
    reopened = DesktopService(service.db.path)
    assert reopened.get_setting('appearance_theme') == mode
    assert reopened.workspaces.settings() == settings
    assert reopened.workspaces.setup_required() == pending
    assert reopened.db.counts() == {'projects': 0, 'batches': 0, 'zips': 0}


@pytest.mark.parametrize('invalid', ['', 'Dark', 'auto', None, True, [], {'mode': 'dark'}])
def test_invalid_appearance_cannot_replace_saved_preference(tmp_path, invalid):
    service = DesktopService(tmp_path / 'data/workbox.db')
    service.set_setting('appearance_theme', 'dark')
    with pytest.raises(ValueError):
        service.set_setting('appearance_theme', invalid)
    assert service.get_setting('appearance_theme') == 'dark'
