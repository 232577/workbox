"use strict";

const copyButton = document.getElementById("copy-checksum");
const checksum = document.getElementById("checksum");
const copyStatus = document.getElementById("copy-status");

copyButton.addEventListener("click", async () => {
  try {
    if (!navigator.clipboard || !window.isSecureContext) {
      throw new Error("Clipboard unavailable");
    }
    await navigator.clipboard.writeText(checksum.textContent.trim());
    copyStatus.textContent = "校验值已复制。";
  } catch {
    copyStatus.textContent = "未能自动复制，请选中上方完整校验值后手动复制。";
  }
});

// 只上报匿名事件类型与随机标识，不收集访客 IP、身份或项目数据。
function reportEvent(type, id = crypto.randomUUID()) {
  const body = JSON.stringify({ type, id });
  fetch('/api/events', { method: 'POST', headers: {'Content-Type':'application/json'},
    body, keepalive: true, credentials: 'same-origin' }).catch(() => {});
}

function downloadIdentity(filename) {
  const storageKey = 'workbox-download-attempt:' + filename;
  try {
    const stored = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (stored && stored.expires > Date.now() && /^[a-f0-9-]{36}$/.test(stored.id)) return stored.id;
    const next = {id:crypto.randomUUID(), expires:Date.now()+30*60*1000};
    sessionStorage.setItem(storageKey, JSON.stringify(next));
    return next.id;
  } catch {return crypto.randomUUID();}
}

document.querySelectorAll('.download-link').forEach(link => {
  const prepare = () => {
    const target = new URL(link.href);
    target.searchParams.set('download', downloadIdentity(target.pathname));
    link.href = target.toString();
  };
  prepare();
  link.addEventListener('click', prepare);
});
document.querySelectorAll('a[href*="github.com/232577/workbox/releases/download/"]').forEach(link => {
  link.addEventListener('click', () => reportEvent('fallback_click'));
});
let viewRecorded = false;
function recordVisiblePage() {
  if (!viewRecorded && document.visibilityState === 'visible') {
    viewRecorded = true;
    reportEvent('page_view');
  }
}
recordVisiblePage();
document.addEventListener('visibilitychange', recordVisiblePage);
