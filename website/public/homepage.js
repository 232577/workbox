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
  const revealHash = (hash, scroll = false) => {
    if (!hash || hash === '#') return;
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
    revealHash(destination.hash);
  });
}

// 图片未完成或不可读时继续显示流程图；两种外观独立确认加载结果。
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
