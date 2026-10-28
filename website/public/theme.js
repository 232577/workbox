"use strict";

// 在样式表加载前确定颜色；浏览器禁止存储时仍能在本页切换。
(() => {
  const key = 'workbox-site-theme';
  const valid = value => ['system', 'light', 'dark'].includes(value);
  let preference = 'system';
  try {
    const saved = window.localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch (_) { /* 私密浏览等环境中保持系统默认。 */ }
  const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const apply = () => {
    const resolved = preference === 'system' ? (media?.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = preference;
    document.documentElement.style.colorScheme = resolved;
  };
  apply();
  if (media?.addEventListener) media.addEventListener('change', apply);
  else if (media?.addListener) media.addListener(apply);
  const connect = () => {
    const select = document.querySelector('[data-theme-select]');
    const control = document.querySelector('[data-theme-control]');
    if (!select || !control) return;
    select.value = preference;
    select.addEventListener('change', () => {
      preference = valid(select.value) ? select.value : 'system';
      select.value = preference;
      apply();
      try { window.localStorage.setItem(key, preference); } catch (_) { /* 不影响当前页面使用。 */ }
    });
    control.hidden = false;
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connect, { once: true });
  else connect();
})();
