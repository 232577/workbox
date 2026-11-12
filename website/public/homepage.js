"use strict";

// 先呈现完整内容，脚本可用时才启用切换；加载失败仍可阅读。
let revealWorkflowTarget = () => {};
const workflowTabs = document.querySelector('[data-workflow-tabs]');
if (workflowTabs) {
  const tabs = Array.from(workflowTabs.querySelectorAll('[data-workflow-tab]'));
  const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')));
  if (tabs.length === 4 && panels.every(Boolean) && new Set(panels).size === 4) {
    const selectTab = (index, focus = false) => {
      tabs.forEach((tab, i) => {
        tab.setAttribute('aria-selected', String(i === index));
        tab.tabIndex = i === index ? 0 : -1;
        panels[i].hidden = i !== index;
      });
      if (focus) tabs[index].focus();
    };
    revealWorkflowTarget = target => {
      const index = panels.findIndex(panel => panel === target || panel.contains(target));
      if (index !== -1) selectTab(index);
    };
    workflowTabs.setAttribute('role', 'tablist');
    tabs.forEach((tab, index) => {
      tab.setAttribute('role', 'tab');
      panels[index].setAttribute('role', 'tabpanel');
      panels[index].setAttribute('aria-labelledby', tab.id);
      panels[index].tabIndex = 0;
      tab.addEventListener('click', () => selectTab(index));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault();
        selectTab(next, true);
      });
    });
    selectTab(0);
    workflowTabs.hidden = false;
  }
}

// 深链接与普通锚点都先展开原生指南，再交给浏览器定位。
if (typeof window !== 'undefined' && window.location) {
  // 静态页面拆分后，旧首页链接仍可能从书签、搜索结果或外部文档进入。
  // 映射固定且只在首页生效；URL fragment不会到达 Worker。
  const legacyPages = Object.freeze({
    'actual-ui-title': '/guide.html', 'try-workbox': '/guide.html', 'try-title': '/guide.html', 'workbox-demo': '/guide.html',
    'narrow-project-preview': '/guide.html', 'online-update': '/guide.html', 'update-recovery': '/guide.html',
    guide: '/guide.html', 'quickstart-video': '/guide.html', 'video-title': '/guide.html',
    'floating-inbox': '/guide.html', 'inbox-title': '/guide.html', workflow: '/guide.html',
    'workflow-title': '/guide.html', 'flow-create': '/guide.html', 'flow-files': '/guide.html',
    'flow-archive': '/guide.html', 'flow-delivery': '/guide.html', features: '/guide.html',
    'features-title': '/guide.html', 'daily-workbench': '/guide.html', 'daily-workbench-title': '/guide.html',
    'pending-project-export': '/guide.html', 'project-batches': '/guide.html',
    'project-batches-title': '/guide.html', 'batch-progress': '/guide.html', settings: '/guide.html',
    'settings-title': '/guide.html', faq: '/guide.html', 'faq-title': '/guide.html',
    'archive-classification': '/guide.html', 'release-history': '/releases.html',
    'release-notes': '/releases.html', 'legacy-task-groups': '/releases.html',
    'task-groups': '/releases.html', 'task-groups-title': '/releases.html',
  });
  const legacyPage = window.location.pathname === '/' || window.location.pathname === '/index.html';
  const redirectLegacyHash = hash => {
    if (!legacyPage || !hash || hash === '#') return false;
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (_) { return false; }
    // An extant target wins; this keeps the compatibility table safe if markup changes later.
    if (document.getElementById(id)) return false;
    if (!Object.hasOwn(legacyPages, id)) return false;
    const page = legacyPages[id];
    window.location.replace(`${page}#${encodeURIComponent(id)}`);
    return true;
  };
  const revealHash = (hash, scroll = false) => {
    if (!hash || hash === '#') return;
    if (redirectLegacyHash(hash)) return;
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch (_) { return; }
    const target = document.getElementById(id);
    if (!target) return;
    for (let element = target; element; element = element.parentElement) {
      if (element.tagName === 'DETAILS') element.open = true;
    }
    revealWorkflowTarget(target);
    if (scroll) target.scrollIntoView();
  };
  revealHash(window.location.hash, true);
  window.addEventListener('hashchange', () => revealHash(window.location.hash, true));
  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.('a[href]');
    if (!link || link.getAttribute('download') !== null) return;
    const target = link.getAttribute('target');
    if (target && target !== '_self') return;
    let destination;
    try { destination = new URL(link.getAttribute('href'), window.location.href); } catch (_) { return; }
    const current = new URL(window.location.href);
    if (destination.origin !== current.origin || destination.pathname !== current.pathname || destination.search !== current.search) return;
    if (redirectLegacyHash(destination.hash)) {
      event.preventDefault();
      return;
    }
    revealHash(destination.hash);
  });
}

// 图片未完成或不可读时显示文字后备；按原始图片确认加载结果。
const desktopStage = document.querySelector('[data-desktop-stage]');
if (desktopStage) {
  desktopStage.querySelectorAll('img[data-desktop-image]').forEach(image => {
    const name = image.dataset.desktopImage;
    if (!['light', 'dark'].includes(name)) return;
    const mark = () => { desktopStage.dataset[name + 'Ready'] = String(image.naturalWidth > 0); };
    desktopStage.dataset[name + 'Ready'] = 'false';
    image.addEventListener('load', mark);
    image.addEventListener('error', () => { desktopStage.dataset[name + 'Ready'] = 'false'; });
    if (image.complete) mark();
  });
}
