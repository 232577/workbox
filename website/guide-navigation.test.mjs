import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('./public/homepage.js', import.meta.url), 'utf8');

function fixture(hash = '', { incompleteTabs = false } = {}) {
  const elements = [];
  class Element {
    constructor(tagName, id = '', parentElement = null, attrs = {}) {
      this.tagName = tagName.toUpperCase(); this.id = id; this.parentElement = parentElement;
      this.attrs = attrs; this.listeners = {}; this.hidden = false; this.open = false;
      this.scrolls = 0; this.dataset = {}; this.tabIndex = 0;
      elements.push(this);
    }
    getAttribute(name) { return name === 'id' ? this.id : this.attrs[name] ?? null; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    addEventListener(type, listener) { (this.listeners[type] ??= []).push(listener); }
    querySelectorAll(selector) { return selector === '[data-workflow-tab]' ? tabs : []; }
    contains(element) { for (; element; element = element.parentElement) if (element === this) return true; return false; }
    closest(selector) { for (let element = this; element; element = element.parentElement) if (selector === 'a[href]' && element.tagName === 'A' && element.attrs.href) return element; return null; }
    scrollIntoView() {
      const ancestors = []; for (let item = this.parentElement; item; item = item.parentElement) ancestors.push(item);
      assert.ok(ancestors.every(item => item.tagName !== 'DETAILS' || item.open), '定位时全部折叠祖先已展开');
      assert.ok(!this.hidden, '定位时目标已可见'); this.scrolls++;
    }
    focus() {}
  }
  const guide = new Element('details', 'guide');
  const nested = new Element('details', 'nested', guide);
  const inbox = new Element('section', 'floating-inbox', nested);
  const batches = new Element('section', 'project-batches', guide);
  const notes = new Element('details', 'release-notes');
  const update = new Element('p', 'online-update', notes);
  const encoded = new Element('p', '收件指南', nested);
  const list = new Element('div', '', guide); list.hidden = true;
  const panels = ['create', 'files', 'archive', 'delivery'].map(name => new Element('div', `flow-${name}`, guide));
  const child = new Element('p', 'file-help', panels[1]);
  const tabs = panels.map((panel, index) => new Element('button', `tab-${index}`, list, { 'aria-controls': panel.id }));
  const documentListeners = {}, windowListeners = {};
  const document = {
    querySelector(selector) { return selector === '[data-workflow-tabs]' ? list : null; },
    getElementById(id) { return incompleteTabs && id === 'flow-delivery' ? null : elements.find(item => item.id === id) ?? null; },
    addEventListener(type, listener) { (documentListeners[type] ??= []).push(listener); },
  };
  const location = { href: `https://workbox.example/${hash}`, hash };
  const window = { location, addEventListener(type, listener) { (windowListeners[type] ??= []).push(listener); } };
  vm.runInNewContext(source, { document, window, URL, console }, { timeout: 1000 });
  const navigate = next => {
    location.hash = next; location.href = `https://workbox.example/${next}`;
    for (const listener of windowListeners.hashchange ?? []) listener();
  };
  const click = (href, options = {}) => {
    const link = new Element('a', '', null, { href, ...options.attrs });
    const icon = new Element('span', '', link);
    const event = { target: icon, button: 0, defaultPrevented: false, ...options };
    for (const listener of documentListeners.click ?? []) listener(event);
    return event;
  };
  return { guide, nested, inbox, batches, notes, update, encoded, list, tabs, panels, child, navigate, click };
}

test('直接深链接打开指南的全部折叠祖先并定位', () => {
  const page = fixture('#floating-inbox');
  assert.equal(page.guide.open, true); assert.equal(page.nested.open, true);
  assert.equal(page.inbox.scrolls, 1);
});

test('hashchange展开指南和下载发布说明，同一hash再次点击仍重新展开', () => {
  const page = fixture();
  page.navigate('#project-batches');
  assert.equal(page.guide.open, true); assert.equal(page.batches.scrolls, 1);
  page.guide.open = false;
  page.click('#project-batches');
  assert.equal(page.guide.open, true);
  page.navigate('#online-update');
  assert.equal(page.notes.open, true); assert.equal(page.update.scrolls, 1);
});

test('深链接选择对应workflow面板，包含面板内部的目标', () => {
  for (const [hash, selected] of [['#flow-files', 1], ['#flow-archive', 2], ['#flow-delivery', 3], ['#file-help', 1]]) {
    const page = fixture(hash);
    page.panels.forEach((panel, index) => assert.equal(panel.hidden, index !== selected));
    page.tabs.forEach((tab, index) => assert.equal(tab.getAttribute('aria-selected'), String(index === selected)));
    assert.equal(page.guide.open, true);
  }
});

test('同页锚点在浏览器默认导航前展开并切换面板，不拦截默认行为', () => {
  const page = fixture();
  const event = page.click('https://workbox.example/#flow-files');
  assert.equal(page.guide.open, true); assert.equal(page.panels[1].hidden, false);
  assert.equal(event.defaultPrevented, false);
});

test('非同页、修改键和新窗口链接不触发指南展开', () => {
  const page = fixture();
  for (const [href, options] of [
    ['https://elsewhere.example/#project-batches', {}], ['/other#project-batches', {}],
    ['?other=1#project-batches', {}], ['#project-batches', { ctrlKey: true }],
    ['#project-batches', { metaKey: true }], ['#project-batches', { button: 1 }],
    ['#project-batches', { defaultPrevented: true }], ['#project-batches', { attrs: { target: '_blank' } }],
    ['#project-batches', { attrs: { download: '' } }],
  ]) page.click(href, options);
  assert.equal(page.guide.open, false);
});

test('编码hash正常定位，空hash、未知id和非法百分号安全忽略', () => {
  const page = fixture('#%E6%94%B6%E4%BB%B6%E6%8C%87%E5%8D%97');
  assert.equal(page.encoded.scrolls, 1);
  for (const hash of ['', '#', '#missing', '#bad%escape']) assert.doesNotThrow(() => page.navigate(hash));
  for (const hash of ['', '#missing', '#bad%escape']) assert.doesNotThrow(() => fixture(hash));
});

test('workflow配置不完整时只展开指南，不隐藏静态面板', () => {
  const page = fixture('#flow-files', { incompleteTabs: true });
  assert.equal(page.guide.open, true); assert.equal(page.list.hidden, true);
  assert.ok(page.panels.every(panel => !panel.hidden));
});
