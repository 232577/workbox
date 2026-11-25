from hashlib import sha256

import pytest
from playwright.sync_api import sync_playwright

from workbox.team_delivery.contracts import GatewayError, Material
from workbox.team_delivery.workbox_upload import fill_manual_form, manual_target
from anonymous_document import make_document


PROJECT = '55bd069e-2aa9-4b49-a382-8553ce0be355'
OTHER = '11bd069e-2aa9-4b49-a382-8553ce0be366'
ORIGIN = 'http://127.0.0.1:35211'
ROUTE = '/?controller=SimpleUploadsController&plugin=WorkBoxIntake&business_id=' + PROJECT + '&action='
TITLE = '匿名手册测试'


def test_only_populates_the_chosen_native_manual_input(tmp_path):
    paths = []
    html = f'''<section data-workbox-simple-page="uploads"><p class="wb-simple-filename"><strong>{TITLE}</strong></p>
        <form method="post" enctype="multipart/form-data" action="{ROUTE}handoffManual">
        <input type="file" name="manual_file"><button type="submit">上传并交接</button></form></section>'''
    source = tmp_path / (TITLE + '.docx')
    make_document(source)
    original = source.read_bytes()
    material = Material(source.name, source, sha256(original).hexdigest())
    with sync_playwright() as app:
        browser = app.chromium.launch(channel='chrome')
        try:
            page = browser.new_page()
            def reply(route):
                paths.append(route.request.method)
                route.fulfill(content_type='text/html; charset=utf-8', body=html)
            page.route('**/*', reply)
            page.goto(ORIGIN + ROUTE + 'show')
            assert manual_target(page, ORIGIN, TITLE) == PROJECT
            with pytest.raises(GatewayError):
                fill_manual_form(page, ORIGIN, TITLE, OTHER, material)
            with pytest.raises(GatewayError):
                manual_target(page, ORIGIN, '另一个项目')
            with pytest.raises(GatewayError):
                manual_target(page, 'http://127.0.0.1:35212', TITLE)
            form = page.locator('form')
            form.evaluate('(f, value) => f.action=value', 'http://127.0.0.1:35212' + ROUTE + 'handoffManual')
            with pytest.raises(GatewayError):
                fill_manual_form(page, ORIGIN, TITLE, PROJECT, material)
            form.evaluate('(f, value) => f.action=value', ROUTE.replace(PROJECT, OTHER) + 'handoffManual')
            with pytest.raises(GatewayError):
                fill_manual_form(page, ORIGIN, TITLE, PROJECT, material)
            form.evaluate('(f, value) => f.action=value', ROUTE + 'handoffManual')
            source.write_bytes(b'changed')
            with pytest.raises(GatewayError):
                fill_manual_form(page, ORIGIN, TITLE, PROJECT, material)
            source.write_bytes(original)
            def switched():
                raise GatewayError('账号已变化')
            with pytest.raises(GatewayError, match='账号已变化'):
                fill_manual_form(page, ORIGIN, TITLE, PROJECT, material, check_accounts=switched)
            assert page.locator('input').evaluate('e => e.files.length') == 0
            selection = fill_manual_form(page, ORIGIN, TITLE, PROJECT, material,
                                         check_accounts=lambda: source.write_bytes(b'replaced while checking accounts'))
            selected = bytes(page.locator('input').evaluate('async e=>Array.from(new Uint8Array(await e.files[0].arrayBuffer()))'))
            assert selected == original
            source.write_bytes(original)
            with pytest.raises(GatewayError):
                fill_manual_form(page, ORIGIN, TITLE, PROJECT, material)
            assert 'POST' not in paths
            page.reload()
            assert not selection.root.exists()
        finally:
            browser.close()
