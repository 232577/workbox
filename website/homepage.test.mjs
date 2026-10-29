import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('./public/homepage.js', import.meta.url), 'utf8').catch(() => '');
const themeSource = await readFile(new URL('./public/theme.js', import.meta.url), 'utf8').catch(() => '');
const tags = [...html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)].map(([, name, raw]) => {
  const attrs = Object.fromEntries([...raw.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)]
    .map(([, key, double, single, bare]) => [key.toLowerCase(), double ?? single ?? bare ?? '']));
  return { name: name.toLowerCase(), attrs };
});
const has = (tag, name) => Object.hasOwn(tag.attrs, name);

test('悬浮入口教程独立可定位，说明拖入材料、操作反馈和运行边界', () => {
  const section = html.match(/<section\b[^>]*id="floating-inbox"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.ok(section, '应有独立的悬浮入口教程');
  assert.ok(tags.some(tag => tag.name === 'a' && tag.attrs.href === '#floating-inbox'));
  assert.ok(html.indexOf('id="quickstart-video"') < html.indexOf('id="floating-inbox"'));
  assert.ok(html.indexOf('id="floating-inbox"') < html.indexOf('id="workflow"'));
  for (const phrase of ['拖到悬浮球，收进工作箱', '项目文字', '整个文件夹', '复制收录', '原件保留',
    '共享盘', '虚拟附件', '网址', '待交付', '当前模式', '按住移动', '右键', '粘贴项目消息',
    '选择文件收进项目', '打开 ZIP 归档目录', '未交付项目数', '进度', '查看', '收起', '取消处理',
    '已完成项', '关闭管理面板', '后台运行', '托盘', '退出 WorkBox']) assert.ok(section.includes(phrase), phrase);
  assert.match(section, /歧义[^。]*核对选择/);
  assert.match(section, /收起[^。]*仅[^。]*提示/);
  assert.match(section, /托盘不可用[^。]*悬浮球/);
  assert.match(section, /退出 WorkBox[^。]*停止/);
  assert.doesNotMatch(section, /<(?:button|input|video)\b/);
});

test('步骤视频使用可访问的原生控件、字幕和备用链接，不自动播放', () => {
  const video = tags.find(tag => tag.name === 'video');
  assert.ok(video);
  for (const attribute of ['controls', 'playsinline']) assert.ok(has(video, attribute));
  assert.ok(!has(video, 'autoplay'));
  assert.equal(video.attrs.preload, 'none');
  assert.equal(video.attrs.width, '1920');
  assert.equal(video.attrs.height, '1080');
  assert.equal(video.attrs.poster, '/media/workbox-workbench-20261029.poster.png');
  assert.ok(video.attrs['aria-label']);
  const base = '/media/workbox-workbench-20261029';
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
  assert.match(html, /39,680,913 字节（约 39\.7 MB）/);
  assert.match(html, /最终原生验证/);
  assert.match(html, /工作台增强版现已发布/);
  assert.match(html, /双主题界面版（历史）/);
  assert.match(html, /4e6dbe36faf3920ef637e0deca797950b596b72cc6f893bc9e78e302d0c5b548/);
  assert.match(html, /下载工作台增强版/);
  assert.ok(links.includes('https://github.com/232577/workbox/blob/main/LICENSE'));
});

test('官网准确说明工作量资格、独立项目类型和响应式工作台边界', () => {
  for (const phrase of ['排除在个人工作量计数之外', '保留“已交付”状态', '最早仍有效交付日',
    '不会把旧项目记到今天', '撤销误记仍按原规则将项目退回“待交付”', '类型与个人工作量计数互不影响',
    '自定义项目类型可独立增改、停用启用、筛选及右键或批量分配', '窄窗可折叠且保留数字徽章',
    '左侧项目、右侧文档分栏与项目右键菜单']) assert.ok(html.includes(phrase), phrase);
  assert.doesNotMatch(html, /原生 WinUI|已全部替换 UI 控件库/);
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

function setupTheme({ stored = null, dark = false, unavailable = false } = {}) {
  const writes = [];
  const root = { dataset: {}, style: {} };
  const select = new Element(); select.value = 'system';
  const control = new Element(); control.hidden = true;
  const media = { matches: dark, addEventListener(type, listener) { this.listener = listener; } };
  const document = { documentElement: root, readyState: 'complete',
    querySelector(selector) { return selector === '[data-theme-select]' ? select : selector === '[data-theme-control]' ? control : null; } };
  const localStorage = { getItem(key) { assert.equal(key, 'workbox-site-theme'); if (unavailable) throw new Error('blocked'); return stored; },
    setItem(key, value) { if (unavailable) throw new Error('blocked'); writes.push([key, value]); } };
  vm.runInNewContext(themeSource, { document, localStorage, window: { matchMedia: () => media, localStorage }, console }, { timeout: 1000 });
  return { root, select, control, media, writes };
}

test('网站主题原生选择有标签，提前初始化且不修改统计页共享样式', () => {
  assert.ok(themeSource.trim(), 'theme.js 必须存在');
  const script = tags.find(tag => tag.name === 'script' && tag.attrs.src?.startsWith('/theme.js'));
  assert.ok(script && !has(script, 'defer') && !has(script, 'async'), '在首个样式表前同步读取偏好');
  assert.ok(html.indexOf(script.attrs.src) < html.indexOf('rel="stylesheet"'));
  const select = tags.find(tag => tag.name === 'select' && has(tag, 'data-theme-select'));
  assert.equal(select?.attrs.id, 'site-theme');
  assert.ok(tags.some(tag => tag.name === 'label' && tag.attrs.for === 'site-theme'));
  for (const value of ['system', 'light', 'dark']) assert.ok(tags.some(tag => tag.name === 'option' && tag.attrs.value === value));
  assert.match(html, /网站外观[^<]*|仅影响本站/);
});

test('主题默认跟随系统，无有效偏好时不写入存储', () => {
  for (const stored of [null, '', 'invalid', 'DARK']) {
    const fixture = setupTheme({ stored, dark: true });
    assert.equal(fixture.root.dataset.theme, 'dark');
    assert.equal(fixture.select.value, 'system');
    assert.equal(fixture.control.hidden, false);
    assert.deepEqual(fixture.writes, []);
  }
});

test('明确选择即时生效并只保存网站主题，重载恢复', () => {
  const fixture = setupTheme({ stored: 'light', dark: true });
  assert.equal(fixture.root.dataset.theme, 'light');
  fixture.select.value = 'dark'; fixture.select.dispatch('change');
  assert.equal(fixture.root.dataset.theme, 'dark');
  assert.deepEqual(fixture.writes, [['workbox-site-theme', 'dark']]);
  assert.equal(setupTheme({ stored: 'dark' }).root.dataset.theme, 'dark');
});

test('系统变化仅影响跟随系统，存储不可用时仍可切换', () => {
  const fixture = setupTheme({ unavailable: true });
  fixture.media.matches = true; fixture.media.listener();
  assert.equal(fixture.root.dataset.theme, 'dark');
  fixture.select.value = 'light'; fixture.select.dispatch('change');
  assert.equal(fixture.root.dataset.theme, 'light');
  fixture.media.matches = false; fixture.media.listener();
  fixture.media.matches = true; fixture.media.listener();
  assert.equal(fixture.root.dataset.theme, 'light');
  assert.deepEqual(fixture.writes, []);
});

test('所有者统计接入同一外观选择，保留私密登录与空数据容器', async () => {
  const stats = await readFile(new URL('./public/stats.html', import.meta.url), 'utf8');
  assert.match(stats, /src="\/theme\.js[^\"]*"/);
  assert.match(stats, /href="\/theme\.css[^\"]*"/);
  assert.ok(stats.indexOf('/theme.js') < stats.indexOf('rel="stylesheet"'));
  assert.match(stats, /class="stats-page"/);
  assert.match(stats, /<label for="site-theme">网站外观<\/label>/);
  assert.match(stats, /<select id="site-theme"[^>]*data-theme-select/);
  for (const id of ['login-form','owner-key','login-submit','toggle-password','dashboard','day-range','refresh','export-csv','data-error','retry','trend-chart','daily-rows','logout']) assert.ok(stats.includes(`id="${id}"`),id);
  assert.match(stats, /type="password" autocomplete="current-password"/);
  assert.match(stats, /name="robots" content="noindex, nofollow, noarchive"/);
  assert.match(stats, /id="today-views"><\/dd>/);
  assert.match(stats, /id="total-downloads"><\/dd>/);
  assert.match(html,/href="https:\/\/github\.com\/232577\/workbox\/blob\/main\/THIRD_PARTY_NOTICES\.md"[^>]*>第三方许可<\/a>/);
});

test('Hero使用实际浅深界面原图链接并保留有尺寸的文件夹流程图后备', () => {
  for (const theme of ['light','dark']) {
    const path=`/media/workbox-desktop-${theme}-workbench-20261029.png`;
    assert.ok(tags.some(tag=>tag.name==='img'&&tag.attrs.src===path&&tag.attrs['data-desktop-image']===theme&&tag.attrs.width==='1120'&&tag.attrs.height==='760'));
    assert.ok(tags.some(tag=>tag.name==='a'&&tag.attrs.href===path));
  }
  assert.ok(tags.some(tag=>has(tag,'data-desktop-stage')));
  assert.match(html,/实际界面 · 示例资料/);
  assert.match(html,/点击图片查看原图/);
  assert.match(html,/wb-preview-fallback/);
  assert.match(html,/wb-folder-front/);
  assert.match(html,/功能流程示意/);
});

function setupPreview({cached=false}={}) {
  const stage=new Element();
  const images=['light','dark'].map(theme=>{const image=new Element();image.dataset.desktopImage=theme;image.complete=cached;image.naturalWidth=cached?1120:0;return image;});
  stage.querySelectorAll=()=>images;
  const document={querySelector:selector=>selector==='[data-desktop-stage]'?stage:null};
  vm.runInNewContext(source,{document,console},{timeout:1000});
  return {stage,images};
}

test('实际截图加载完成才替换流程图，失败和缓存命中各自正确回退', () => {
  const {stage,images}=setupPreview();
  assert.equal(stage.dataset.lightReady,'false');
  assert.equal(stage.dataset.darkReady,'false');
  images[0].naturalWidth=1120;images[0].dispatch('load');
  assert.equal(stage.dataset.lightReady,'true');
  assert.equal(stage.dataset.darkReady,'false');
  images[0].naturalWidth=0;images[0].dispatch('error');
  assert.equal(stage.dataset.lightReady,'false');
  assert.equal(setupPreview({cached:true}).stage.dataset.darkReady,'true');
});
