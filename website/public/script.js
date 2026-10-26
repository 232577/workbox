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
