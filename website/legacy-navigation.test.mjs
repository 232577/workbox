import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('./public/homepage.js', import.meta.url), 'utf8');

function run({ page = '/', hash = '', ids = [] } = {}) {
  const elements = new Map(ids.map(id => [id, { id, parentElement: null, tagName: 'SECTION', open: false, scrollIntoView() {} }]));
  const documentListeners = {};
  const windowListeners = {};
  const replacements = [];
  const document = {
    querySelector: () => null,
    getElementById: id => elements.get(id) ?? null,
    addEventListener(type, listener) { (documentListeners[type] ??= []).push(listener); },
  };
  const location = {
    href: `https://workbox.example${page}${hash}`,
    pathname: page,
    hash,
    origin: 'https://workbox.example',
    replace(value) { replacements.push(value); },
  };
  const window = { location, addEventListener(type, listener) { (windowListeners[type] ??= []).push(listener); } };
  vm.runInNewContext(source, { document, window, URL, console }, { timeout: 1000 });
  const navigate = next => {
    location.hash = next;
    location.href = `https://workbox.example${page}${next}`;
    for (const listener of windowListeners.hashchange ?? []) listener();
  };
  const click = (href, options = {}) => {
    const attrs = options.attrs ?? {};
    const link = { getAttribute: name => name === 'href' ? href : attrs[name] ?? null };
    const event = { target: { closest: selector => selector === 'a[href]' ? link : null }, button: 0, defaultPrevented: false, ...options,
      preventDefault() { this.defaultPrevented = true; } };
    for (const listener of documentListeners.click ?? []) listener(event);
    return event;
  };
  return { replacements, navigate, click };
}

test('首页旧内容锚点迁往固定指南/版本页并保留原片段', () => {
  const guideIds = ['actual-ui-title', 'try-workbox', 'try-title', 'workbox-demo', 'narrow-project-preview', 'online-update', 'update-recovery', 'guide',
    'quickstart-video', 'video-title', 'floating-inbox', 'inbox-title', 'workflow', 'workflow-title', 'flow-create', 'flow-files',
    'flow-archive', 'flow-delivery', 'features', 'features-title', 'daily-workbench', 'daily-workbench-title',
    'pending-project-export', 'project-batches', 'project-batches-title', 'batch-progress', 'settings', 'settings-title',
    'faq', 'faq-title', 'archive-classification'];
  for (const id of guideIds) {
    const page = run({ hash: `#${id}` });
    assert.deepEqual(page.replacements, [`/guide.html#${id}`], id);
  }
  for (const id of ['release-history', 'release-notes', 'legacy-task-groups', 'task-groups', 'task-groups-title']) {
    const page = run({ hash: `#${id}` });
    assert.deepEqual(page.replacements, [`/releases.html#${id}`], id);
  }
});

async function runSiteScript({ controls = false, clipboard = true } = {}) {
  const events = [];
  const listeners = {};
  const copyHandlers = [];
  const button = { addEventListener(type, fn) { if (type === 'click') copyHandlers.push(fn); } };
  const checksum = { textContent: '  abc123  ' };
  const status = { textContent: '' };
  const download = {
    href: 'https://workbox.example/download/setup.exe',
    addEventListener(type, fn) { (listeners.download ??= []).push(fn); },
  };
  const document = {
    visibilityState: 'visible',
    getElementById(id) { if (!controls) return null; return id === 'copy-checksum' ? button : id === 'checksum' ? checksum : id === 'copy-status' ? status : null; },
    querySelectorAll(selector) { return selector === '.download-link' ? [download] : []; },
    addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
  };
  const window = { isSecureContext: clipboard };
  const navigator = clipboard ? { clipboard: { async writeText(value) { events.push(['clipboard', value]); } } } : {};
  const context = {
    document, window, navigator, URL, Date,
    crypto: { randomUUID: () => '12345678-1234-1234-1234-123456789abc' },
    sessionStorage: { getItem: () => null, setItem() {} },
    fetch: async (url, options) => { events.push(['fetch', url, options.body]); },
  };
  vm.runInNewContext(await readFile(new URL('./public/script.js', import.meta.url), 'utf8'), context, { timeout: 1000 });
  return { events, listeners, copyHandlers, download, status };
}

test('指南页缺少校验复制控件时仍初始化匿名统计与下载身份', async () => {
  const page = await runSiteScript();
  assert.equal(page.copyHandlers.length, 0);
  assert.equal(page.events[0][0], 'fetch');
  assert.match(page.events[0][2], /"type":"page_view"/);
  assert.match(page.download.href, /download=12345678-1234-1234-1234-123456789abc/);
  page.listeners.download[0]();
  assert.match(page.download.href, /download=12345678-1234-1234-1234-123456789abc/);
});

test('校验值复制成功与失败反馈仍可用', async () => {
  const success = await runSiteScript({ controls: true });
  await success.copyHandlers[0]();
  assert.deepEqual(success.events.find(event => event[0] === 'clipboard'), ['clipboard', 'abc123']);
  assert.equal(success.status.textContent, '校验值已复制。');
  const failure = await runSiteScript({ controls: true, clipboard: false });
  await failure.copyHandlers[0]();
  assert.match(failure.status.textContent, /未能自动复制/);
});

test('旧锚点在初次加载、hashchange 和普通点击时兼容跳转', () => {
  const initial = run({ hash: '#floating-inbox' });
  assert.deepEqual(initial.replacements, ['/guide.html#floating-inbox']);
  const changed = run();
  changed.navigate('#task-groups');
  assert.deepEqual(changed.replacements, ['/releases.html#task-groups']);
  const clicked = run();
  const event = clicked.click('#batch-progress');
  assert.deepEqual(clicked.replacements, ['/guide.html#batch-progress']);
  assert.equal(event.defaultPrevented, true);
});

test('仅首页兼容已知旧锚点；未知、非法、保留锚点和外站不跳转', () => {
  for (const hash of ['#missing', '#bad%escape', '#toString', '#constructor', '#__proto__', '#top', '#main', '#overview', '#overview-title', '#hero-title', '#actual-ui',
    '#download', '#download-title', '#support', '#checksum', '#copy-checksum', '#copy-status']) {
    assert.deepEqual(run({ hash }).replacements, [], hash);
  }
  assert.deepEqual(run({ page: '/guide.html', hash: '#floating-inbox' }).replacements, []);
  const external = run();
  external.click('https://elsewhere.example/#floating-inbox');
  assert.deepEqual(external.replacements, []);
});

test('映射目标若仍存在则优先原地展开；未知外站、查询串和下载链接不被劫持', () => {
  const extant = run({ hash: '#floating-inbox', ids: ['floating-inbox'] });
  assert.deepEqual(extant.replacements, []);
  const page = run();
  for (const [href, options] of [
    ['https://elsewhere.example/#floating-inbox', {}],
    ['/?campaign=other#floating-inbox', {}],
    ['#floating-inbox', { attrs: { download: '' } }],
    ['#floating-inbox', { attrs: { target: '_blank' } }],
    ['#floating-inbox', { ctrlKey: true }],
    ['#floating-inbox', { metaKey: true }],
    ['#floating-inbox', { shiftKey: true }],
    ['#floating-inbox', { altKey: true }],
    ['#floating-inbox', { button: 1 }],
    ['#floating-inbox', { defaultPrevented: true }],
  ]) {
    const event = page.click(href, options);
    assert.equal(event.defaultPrevented, Boolean(options.attrs?.download) ? false : Boolean(options.defaultPrevented), href);
  }
  assert.deepEqual(page.replacements, []);
});
