import subprocess
import sys
from textwrap import dedent


def test_qt_office_modules_import_without_playwright():
    script = dedent('''
        import sys

        sys.modules['playwright'] = None
        from workbox.team_delivery import office_connection, office_form, office_panel
        from workbox.team_delivery import office_identity

        assert 'workbox.team_delivery.workbox_identity' not in sys.modules
        origin = office_panel.normalize_workbox_url(' HTTP://127.0.0.1:80/ ')
        account = office_panel.WorkBoxAccount(origin, '17', 'anonymous-member')
        assert origin == 'http://127.0.0.1'
        assert type(account) is office_identity.WorkBoxAccount
        assert office_connection._READ_PROFILE is office_identity._READ_PROFILE
        assert office_form._PROFILE_LINK is office_identity._PROFILE_LINK
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stderr


def test_legacy_workbox_identity_reexports_the_shared_helpers():
    from workbox.team_delivery import office_identity, workbox_identity

    for name in ('WorkBoxAccount', 'normalize_workbox_url', '_PROFILE_PATH', '_PROFILE_LINK', '_READ_PROFILE'):
        assert getattr(workbox_identity, name) is getattr(office_identity, name)
