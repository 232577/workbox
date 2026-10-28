import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('./public/homepage.js', import.meta.url), 'utf8').catch(() => '');
const tags = [...html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)].map(([, name, raw]) => {
  const attrs = Object.fromEntries([...raw.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)]
    .map(([, key, double, single, bare]) => [key.toLowerCase(), double ?? single ?? bare ?? '']));
  return { name: name.toLowerCase(), attrs };
});
const has = (tag, name) => Object.hasOwn(tag.attrs, name);

test('步骤视频使用可访问的原生控件、字幕和备用链接，不自动播放', () => {
  const video = tags.find(tag => tag.name === 'video');
  assert.ok(video);
  for (const attribute of ['controls', 'playsinline']) assert.ok(has(video, attribute));
  assert.ok(!has(video, 'autoplay'));
  assert.equal(video.attrs.preload, 'none');
  assert.equal(video.attrs.width, '1920');
  assert.equal(video.attrs.height, '1080');
  assert.equal(video.attrs.poster, '/media/workbox-quickstart-20261028.poster.png');
  assert.ok(video.attrs['aria-label']);
  const base = '/media/workbox-quickstart-20261028';
  assert.ok(tags.some(tag => tag.name === 'source' && tag.attrs.src === `${base}.mp4`));
  assert.ok(tags.some(tag => tag.name === 'track' && tag.attrs.src === `${base}.vtt` && tag.attrs.kind === 'captions' && tag.attrs.srclang === 'zh'));
  assert.ok(tags.some(tag => tag.name === 'a' && tag.attrs.href === `${base}.mp4`));
  assert.match(html, /截图步骤演示/);
  assert.match(html, /无声/);
  assert.match(html, /不是连续录屏/);
  assert.ok(html.indexOf('id="quickstart-video"') < html.indexOf('id="workflow"'));
});

test('首次使用推荐通用模式，自动交付门槛只适用于自动模式', () => {
  assert.match(html, /推荐通用模式/);
  assert.match(html, /人工确认交付/);
  assert.match(html, /自动模式[^<。]*4/);
  assert.match(html, /升级[^<。]*原有配置/);
  assert.doesNotMatch(html, /ZIP 默认按/);
});

test('首页使用中文、唯一主标题且每个内部锚点都指向唯一元素', () => {
  assert.equal(tags.find(tag => tag.name === 'html').attrs.lang, 'zh-CN');
  assert.equal(tags.filter(tag => tag.name === 'h1').length, 1);
  const ids = tags.filter(tag => has(tag, 'id')).map(tag => tag.attrs.id);
  assert.equal(new Set(ids).size, ids.length, 'id 不得重复');
  for (const tag of tags.filter(tag => tag.attrs.href?.startsWith('#'))) {
    assert.equal(ids.filter(id => id === tag.attrs.href.slice(1)).length, 1, tag.attrs.href);
  }
});

test('新旧安装包和 GitHub 备用源保持发布目录中的真实地址，校验值一致', () => {
  const links = tags.filter(tag => tag.name === 'a').map(tag => tag.attrs.href);
  for (const item of releases) assert.ok(links.includes(`/download/${item.filename}`), item.filename);
  assert.ok(links.includes(`https://github.com/232577/workbox/releases/download/${release.tag}/${release.filename}`));
  const checksum = html.match(/<code\b[^>]*\bid=["']checksum["'][^>]*>([\s\S]*?)<\/code>/i)?.[1].trim();
  assert.equal(checksum, release.sha256);
  for (const id of ['copy-checksum', 'copy-status']) assert.ok(tags.some(tag => tag.attrs.id === id), id);
});

test('下载统计和赞助沿用实际脚本及关闭的付款配置', async () => {
  for (const path of ['/script.js', '/support.js', '/homepage.js']) {
    assert.ok(tags.some(tag => tag.name === 'script' && tag.attrs.src && new URL(tag.attrs.src, 'https://workbox.ltxiangzi.dpdns.org').pathname === path), path);
  }
  assert.ok(tags.some(tag => tag.name === 'a' && tag.attrs.href === '/stats/'));
  assert.ok(tags.some(tag => tag.attrs.id === 'support' && has(tag, 'hidden')));
  assert.ok(tags.filter(tag => has(tag, 'data-support-link')).every(tag => has(tag, 'hidden')));
  assert.deepEqual(JSON.parse(await readFile(new URL('./public/support.json', import.meta.url), 'utf8')), { enabled: false, methods: [] });
});

test('工作流程四块内容在不执行 JavaScript 时全部可读，控件渐进出现', () => {
  const tabs = tags.filter(tag => has(tag, 'data-workflow-tab'));
  const panels = tags.filter(tag => has(tag, 'data-workflow-panel'));
  const lists = tags.filter(tag => has(tag, 'data-workflow-tabs'));
  assert.equal(lists.length, 1);
  assert.ok(has(lists[0], 'hidden'));
  assert.equal(tabs.length, 4);
  assert.equal(panels.length, 4);
  for (const [index, tab] of tabs.entries()) {
    assert.equal(tab.name, 'button');
    assert.equal(tab.attrs.type, 'button');
    assert.equal(tab.attrs['aria-controls'], panels[index].attrs.id);
  }
  for (const panel of panels) {
    assert.ok(panel.attrs.id);
    assert.ok(!has(panel, 'hidden'), panel.attrs.id);
    assert.ok(!/display\s*:\s*none|visibility\s*:\s*hidden/i.test(panel.attrs.style ?? ''));
  }
});

// 小型事件宿主只模拟浏览器接口；行为断言通过事件和可观察状态完成。
class Element {
  constructor(attrs = {}) {
    this.attrs = { ...attrs }; this.hidden = false; this.tabIndex = 0;
    this.listeners = new Map(); this.dataset = {}; this.children = [];
    this.id = attrs.id ?? ''; this.classes = new Set();
    this.classList = { add: value => this.classes.add(value), remove: value => this.classes.delete(value),
      toggle: (value, force) => { const enabled = force ?? !this.classes.has(value); enabled ? this.classes.add(value) : this.classes.delete(value); return enabled; } };
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = String(value); if (name === 'tabindex') this.tabIndex = Number(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  querySelectorAll(selector) { return this.children.filter(element => selector === '[data-workflow-tab]' && Object.hasOwn(element.attrs, 'data-workflow-tab')); }
  focus() { this.ownerDocument.activeElement = this; }
  dispatch(type, key) {
    const event = { type, key, target: this, currentTarget: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const listener of this.listeners.get(type) ?? []) listener(event);
    return event;
  }
}

function setup({ missingPanel = false } = {}) {
  const list = new Element({ 'data-workflow-tabs': '' }); list.hidden = true;
  const tabs = Array.from({ length: 4 }, (_, index) => new Element({ id: `tab-${index}`, 'data-workflow-tab': '', 'aria-controls': `panel-${index}` }));
  const panels = Array.from({ length: missingPanel ? 3 : 4 }, (_, index) => new Element({ id: `panel-${index}`, 'data-workflow-panel': '' }));
  list.children = tabs;
  const elements = [list, ...tabs, ...panels];
  const document = {
    readyState: 'complete', activeElement: null,
    querySelectorAll(selector) { return elements.filter(element => Object.hasOwn(element.attrs, selector.slice(1, -1))); },
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
    getElementById(id) { return elements.find(element => element.id === id) ?? null; },
    addEventListener(type, listener) { if (type === 'DOMContentLoaded') listener(); },
  };
  elements.forEach(element => { element.ownerDocument = document; });
  const context = { document, console, window: { document }, HTMLElement: Element };
  vm.runInNewContext(source, context, { filename: 'homepage.js', timeout: 1000 });
  return { list, tabs, panels, document };
}

function assertSelected(fixture, selected) {
  fixture.tabs.forEach((tab, index) => {
    assert.equal(tab.getAttribute('aria-selected'), String(index === selected));
    assert.equal(tab.tabIndex, index === selected ? 0 : -1);
    assert.equal(fixture.panels[index].hidden, index !== selected);
  });
}

test('完整四面板才能增强为可访问的 tabs，首次只显示第一块', () => {
  assert.ok(source.trim(), 'homepage.js 必须存在');
  const fixture = setup();
  assert.equal(fixture.list.hidden, false);
  assert.equal(fixture.list.getAttribute('role'), 'tablist');
  fixture.tabs.forEach(tab => assert.equal(tab.getAttribute('role'), 'tab'));
  fixture.panels.forEach(panel => assert.equal(panel.getAttribute('role'), 'tabpanel'));
  assertSelected(fixture, 0);
});

test('点击和方向键、Home、End 同步选择、焦点和可访问状态', () => {
  const fixture = setup();
  fixture.tabs[2].dispatch('click'); assertSelected(fixture, 2);
  for (const [from, key, target] of [[2, 'ArrowRight', 3], [3, 'ArrowRight', 0], [0, 'ArrowLeft', 3], [3, 'Home', 0], [0, 'End', 3]]) {
    const event = fixture.tabs[from].dispatch('keydown', key);
    assert.equal(event.defaultPrevented, true, key);
    assert.equal(fixture.document.activeElement, fixture.tabs[target], key);
    assertSelected(fixture, target);
  }
  assert.equal(fixture.tabs[3].dispatch('keydown', 'Tab').defaultPrevented, false);
});

test('面板缺失时保留静态内容，不显示失效 tabs 或隐藏任何面板', () => {
  const fixture = setup({ missingPanel: true });
  assert.equal(fixture.list.hidden, true);
  assert.ok(fixture.panels.every(panel => !panel.hidden));
  assert.ok(fixture.tabs.every(tab => tab.getAttribute('role') !== 'tab'));
});
