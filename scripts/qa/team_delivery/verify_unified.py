"""Run the unified WorkBox Windows handoff against a disposable installed Kanboard runtime."""

import argparse
from collections import Counter
import hashlib
from importlib.util import find_spec
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from installed_server import assert_closed, installed_server, remove_owned_directory


def default_source():
    return next((parent for parent in Path(__file__).resolve().parents
                 if (parent / 'workbox/__main__.py').is_file()), None)


MANUAL_TEXT = ('匿名安装验收操作手册', '打开本地项目表。', '选择匿名项目并查看文件。', '核对本次制作得到的成果。')
CODE_TITLE = '匿名代码样例'
CODE_COMMENT = '# 中文注释验证代码成果'
CODE_LINES = tuple(f'check_{index:02d} = {index} + 1' for index in range(1, 12))
PUBLIC_ASSETS = ('public-index.html', 'public-app.js', 'app.css', 'dashboard.js', 'excel-export.js', 'notifications.js')


def checkout_identity(source):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=source))
    return commit, dirty


def assets_identity(assets):
    evidence = {}
    for name in PUBLIC_ASSETS:
        data = (assets / name).read_bytes()
        if not data:
            raise ValueError(f'Empty offline original-site asset: {name}')
        evidence[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return evidence


def write_results(output, evidence):
    pending = output / 'results.json.partial'
    pending.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(output / 'results.json')


def verify_pdf_text(archive_path, project, language):
    from pypdf import PdfReader

    expected = {project + '.pdf':MANUAL_TEXT,
                f'{project}_代码（{language}）.pdf':(CODE_TITLE, CODE_COMMENT, *CODE_LINES)}
    evidence = {}
    with ZipFile(archive_path) as archive:
        for name, markers in expected.items():
            data = archive.read(name)
            reader = PdfReader(BytesIO(data))
            text = '\n'.join(page.extract_text() for page in reader.pages)
            compact = ''.join(text.split())
            assert all(''.join(marker.split()) in compact for marker in markers), (name, text)
            evidence[name] = {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest(),
                              'pages':len(reader.pages), 'text':text}
    return evidence


def inspect_worker_scratch(state, database, evidence, *, require_pruned):
    manifest = json.loads((state / 'manifest.json').read_text(encoding='utf-8'))
    root = Path(manifest['worker']['work_root']).resolve(strict=True)
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        attempts = connection.execute('''SELECT id, attempt_id FROM workbox_production_packages WHERE project_id=?''',
                                      (evidence['business_id'],)).fetchall()
    (package_id, attempt_id), = attempts
    assert package_id == evidence['package_id']
    attempt = root / attempt_id
    assert attempt.parent == root
    temp = attempt / 't'
    scratch = temp / 'attempt'
    deadline = monotonic() + (12 if require_pruned else 0)
    observation = None
    while True:
        receipt = json.loads((state / 'control/receipt.json').read_text(encoding='utf-8'))
        assert receipt['state'] == 'running' and receipt['worker']['private_ready']
        runner = receipt['worker']['runner_id']
        logs = (root / runner / 'observations.jsonl').read_text(encoding='utf-8')
        complete_lines = logs.splitlines() if logs.endswith('\n') else logs.splitlines()[:-1]
        for line in reversed(complete_lines):
            event = json.loads(line)
            if (event['phase'] == 'scratch_cleanup_complete' and event['attempt_id'] == attempt_id
                    and event['owner_run_id'] == receipt['run_id'] and event['runner_id'] == runner):
                observation = event
                break
        if observation is not None or monotonic() >= deadline:
            break
        sleep(0.2)
    copies = list(scratch.glob('scratch-*/source.docx'))
    temp_files = [file for file in temp.rglob('*') if file.is_file()]
    actual = {'attempt_id':attempt_id, 'outer_input_and_output_preserved':(attempt / 'i').is_dir() and (attempt / 'o').is_dir(),
              'temporary_files':len(temp_files), 'converter_word_copies':len(copies),
              'converter_word_bytes':sum(file.stat().st_size for file in copies), 'attempt_scratch_absent':not scratch.exists(),
              'product_cleanup_observation':observation}
    if require_pruned:
        assert actual['outer_input_and_output_preserved'] and not scratch.exists() and not temp_files and observation, actual
    return actual


def verify_combined_xu(playwright, chromium, business, base, password, evidence, database, facts, output):
    from playwright.sync_api import expect

    package = evidence['package_id']
    project = evidence['business_id']
    submissions = business.submission_events(database, package)
    assert len(submissions) == 1 and submissions[0][-1] == business.SUBMISSION_NOTE
    browser = playwright.chromium.launch(executable_path=chromium)

    def new_page():
        context = browser.new_context(service_workers='block')
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
        return context.new_page()

    try:
        admin = new_page()
        business.login(admin, base, password)
        user_id, username, account_password = business.create_role(admin, base, 'qa-xu-dual-' + uuid4().hex[:8], 'can_code')
        access = business.route(base, 'AccessController', 'WorkBoxAccess', user_id=user_id)
        admin.goto(access)
        expect(admin.locator('[name=can_code]')).to_be_checked()
        admin.locator('[name=can_submit]').check()
        admin.locator('[name=reason]').fill('匿名验收兼任代码和提交职责')
        admin.get_by_role('button', name='保存团队能力', exact=True).click()
        admin.goto(access)
        for capability in ('can_code', 'can_submit'):
            expect(admin.locator(f'[name={capability}]')).to_be_checked()
        for capability in ('can_manual', 'can_dispatch'):
            expect(admin.locator(f'[name={capability}]')).not_to_be_checked()
        assert int(user_id) not in {account['id'] for account in evidence['operator_accounts'].values()}

        xu = new_page()
        business.login(xu, base, account_password, username=username)
        code = business.route(base, 'WorkflowController', 'WorkBoxIntake', action='code')
        assert xu.goto(code).ok
        expect(xu.get_by_role('link', name='代码待领取', exact=True)).to_be_visible()
        xu.get_by_role('link', name='代码待领取', exact=True).click()
        assert 'filter=queue' in xu.url
        xu.screenshot(path=output / 'xu-dual-code-entry.png')

        files = business.route(base, 'SimpleFilesController', 'WorkBoxIntake', business_id=project)
        assert xu.goto(files).ok
        download = xu.get_by_role('link', name='下载五文件 ZIP', exact=True)
        expect(download).to_be_visible()
        assert parse_qs(urlsplit(download.get_attribute('href')).query)['package_id'] == [package]
        with xu.expect_download() as downloaded:
            download.click()
        assert business.sha256(downloaded.value.path()) == evidence['zip_sha256']
        xu.get_by_role('link', name='外部提交登记', exact=True).click()
        expect(xu.get_by_role('heading', name=evidence['project'], exact=True)).to_be_visible()
        expect(xu.get_by_text('已登记外部提交', exact=False).first).to_be_visible()
        assert package in xu.url
        xu.screenshot(path=output / 'xu-dual-submission-entry.png')
    finally:
        browser.close()

    assert business.production_facts(database, project) == facts
    assert business.submission_events(database, package) == submissions
    return {'username':username, 'user_id':int(user_id), 'code_entry_opened':True,
            'submission_entry_opened':True, 'downloaded_zip_sha256':evidence['zip_sha256'],
            'workload_unchanged':True, 'submission_records_unchanged':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-source', type=Path, default=default_source())
    parser.add_argument('--online-source', type=Path, required=True)
    parser.add_argument('--site-assets', type=Path, required=True, help='Offline copy of the public original frontend')
    parser.add_argument('--program', type=Path, required=True, help='Installed Kanboard program directory')
    parser.add_argument('--playwright-packages', type=Path, help='Optional separate Playwright site-packages')
    parser.add_argument('--pdf-packages', type=Path, help='Optional separate location of the pypdf package')
    parser.add_argument('--chromium', type=Path, required=True, help='Path to Edge or another Chromium executable')
    parser.add_argument('--output', type=Path, required=True, help='Parent directory for a fresh anonymous run')
    parser.add_argument('--worker-parent', type=Path, help='Optional short parent directory for disposable Filee work')
    parser.add_argument('--expected-desktop-commit', help='Require a clean checkout at this exact commit')
    parser.add_argument('--expected-online-commit', help='Require the anonymous Online test checkout at this exact commit')
    parser.add_argument('--expected-server-commit', help='Require the installed server to report this exact commit')
    parser.add_argument('--expected-server-bundle-sha256', help='Verify the restored install-bundle.json before startup')
    parser.add_argument('--require-scratch-pruned', action='store_true',
                        help='Require the running worker to remove completed publication scratch before QA shutdown')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if args.desktop_source is None:
        parser.error('Provide the desktop checkout with --desktop-source.')
    desktop = args.desktop_source.resolve(strict=True)
    online = args.online_source.resolve(strict=True)
    output_root = args.output.resolve()
    if any(output_root.is_relative_to(repo) and not output_root.is_relative_to(repo / '.local')
           for repo in (desktop, online)):
        parser.error('Put acceptance evidence outside the source checkouts or in their ignored .local directory.')
    desktop_commit, desktop_dirty = checkout_identity(desktop)
    if args.expected_desktop_commit and (args.expected_desktop_commit != desktop_commit or desktop_dirty):
        parser.error('The desktop checkout must be clean and match --expected-desktop-commit.')
    online_commit, online_dirty = checkout_identity(online)
    if args.expected_online_commit and (args.expected_online_commit != online_commit or online_dirty):
        parser.error('The Online test checkout must be clean and match --expected-online-commit.')
    sys.path[:0] = [str(desktop), str(online / 'tests/kanboard'), str(online)]
    if find_spec('workbox.team_delivery.qt_site') is None:
        parser.error('Install the integrated desktop with QtSiteSession and QtSiteGateway before joint acceptance.')
    if sys.platform != 'win32' or os.environ.get('QT_QPA_PLATFORM', 'windows') != 'windows':
        parser.error('Run joint acceptance on the interactive Windows Qt platform.')
    qa_commit, qa_dirty = checkout_identity(default_source())
    assets = args.site_assets.resolve(strict=True)
    asset_provenance = assets_identity(assets)
    chromium = args.chromium.resolve(strict=True)
    program = args.program.resolve(strict=True)
    server_bundle_hash = hashlib.sha256((program / 'install-bundle.json').read_bytes()).hexdigest()
    if args.expected_server_bundle_sha256 and args.expected_server_bundle_sha256.lower() != server_bundle_hash:
        parser.error('The restored install-bundle.json differs from --expected-server-bundle-sha256.')
    # The embedded engine may connect directly to the loopback fixtures only.
    # Give every other network destination an unavailable loopback proxy.
    assert_closed(1)
    os.environ.pop('QTWEBENGINE_REMOTE_DEBUGGING', None)
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
        '--disable-gpu --disable-background-networking --proxy-server=http://127.0.0.1:1 --proxy-bypass-list=127.0.0.1'
    )
    if args.playwright_packages:
        sys.path.append(str(args.playwright_packages.resolve(strict=True)))
    if args.pdf_packages:
        sys.path.append(str(args.pdf_packages.resolve(strict=True)))
    import simple_workspace_installed as business
    from playwright.sync_api import sync_playwright
    import pypdf
    from unified_window import unified_window

    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='unified-', dir=output_root)).resolve(strict=True)
    private = output / 'desktop-state'
    private.mkdir()
    lifecycle = {}
    cleanup = output / 'lifecycle.json'
    evidence = {}
    failure = None
    server_commit = None
    completed = False
    try:
        print(f'Verifying and preparing the restored anonymous office server: {output}', flush=True)
        with installed_server(program, output, lifecycle, args.worker_parent) as (state, password):
            print('Anonymous office server and worker ready.', flush=True)
            pwfile = state / 'anonymous-admin.txt'
            with pwfile.open('x', encoding='utf-8') as stream:
                stream.write(password)
            try:
                with patch.object(business, 'QA_ROOT', output):
                    base, server_commit, password = business.installed_identity(state, pwfile)
                if args.expected_server_commit and args.expected_server_commit != server_commit:
                    raise RuntimeError('The installed server differs from --expected-server-commit.')
            finally:
                pwfile.unlink()
                lifecycle['administrator_password_file_removed'] = not pwfile.exists()
            materials = business.anonymous_sources(output)
            from document_fixtures import make_docx

            make_docx(materials[1], title=CODE_TITLE, paragraphs=[CODE_COMMENT, *CODE_LINES], table=False)
            database = state / 'data/database.sqlite'
            passwords = {}
            create_role = business.create_role
            def remember_anonymous_role(*args, **kwargs):
                created = create_role(*args, **kwargs)
                passwords[created[1]] = created[2]
                return created
            with sync_playwright() as playwright, patch.object(business, 'create_role', remember_anonymous_role):
                evidence = business.browser_test(playwright, chromium, base, password,
                                                 materials, output, args.timeout, state, False,
                                                 desktop_window=unified_window(private, lifecycle, assets, passwords))
                facts = business.production_facts(database, evidence['business_id'])
                evidence['worker_scratch_while_server_running'] = inspect_worker_scratch(
                    state, database, evidence, require_pruned=args.require_scratch_pruned)
                evidence['combined_xu_check'] = verify_combined_xu(playwright, chromium, business, base, password,
                                                                  evidence, database, facts, output)
            assert evidence['desktop_window']['personal_word_sha256'] == evidence['code_manual_download']['sha256']
            counts = Counter(kind for kind, _ in facts)
            assert counts == {'manual_first':1, 'code_first':1, 'project_first':1}, facts
            evidence['workload_event_counts'] = dict(counts)
            evidence['verified_pdf_text'] = verify_pdf_text(output / 'generated.zip', evidence['project'], business.LANGUAGE)
            evidence['pdf_text_reader'] = f'pypdf {pypdf.__version__}'
        completed = True
    except Exception as error:
        failure = type(error).__name__
        raise
    finally:
        try:
            if not lifecycle.get('qt_close_pending', False):
                remove_owned_directory(private, output)
                lifecycle['desktop_state_removed'] = True
        finally:
            cleanup.write_text(json.dumps(lifecycle, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            evidence.update(server_commit=server_commit, online_test_commit=online_commit,
                            server_program=str(program), server_install_bundle_sha256=server_bundle_hash,
                            online_test_working_tree_had_changes=online_dirty, desktop_commit=desktop_commit,
                            desktop_working_tree_had_changes=desktop_dirty, qa_commit=qa_commit,
                            qa_working_tree_had_changes=qa_dirty, original_public_assets=asset_provenance,
                            lifecycle=lifecycle, failure_type=failure,
                            acceptance_complete=completed and lifecycle.get('desktop_state_removed', False))
            write_results(output, evidence)
    print(f'PASS unified WorkBox personal-to-team handoff, production and ZIP download: {output}')


if __name__ == '__main__':
    main()
