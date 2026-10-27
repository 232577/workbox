"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const number = new Intl.NumberFormat("zh-CN");
  const time = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "medium", hour12: false });
  let snapshot = null;
  let loading = false;
  let authenticated = false;
  let activeRequest = null;
  let requestVersion = 0;
  let composing = false;

  function status(message) { $("session-status").textContent = message; }
  function setBusy(busy) {
    loading = busy;
    $("dashboard").setAttribute("aria-busy", String(busy));
    ["refresh", "retry", "day-range", "export-csv"].forEach((id) => { $(id).disabled = busy || (id === "export-csv" && !snapshot); });
  }
  function clearData() {
    snapshot = null;
    $("data-content").hidden = true;
    $("daily-rows").replaceChildren();
    $("trend-chart").replaceChildren();
    ["today-date", "today-views", "today-downloads", "today-fallback", "total-views", "total-downloads", "total-fallback", "trend-range"].forEach((id) => { $(id).textContent = ""; });
    $("updated-at").textContent = "尚未取得统计数据";
    $("export-csv").disabled = true;
  }
  function showLogin(message, focus = false) {
    authenticated = false;
    clearData();
    $("dashboard").hidden = true;
    $("logout").hidden = true;
    $("login-panel").hidden = false;
    $("data-error").hidden = true;
    document.title = "登录网站统计 · WorkBox 工作箱";
    status(message);
    if (focus) $("owner-key").focus();
  }
  function showDashboard() {
    authenticated = true;
    $("login-panel").hidden = true;
    $("dashboard").hidden = false;
    $("logout").hidden = false;
    document.title = "网站统计 · WorkBox 工作箱";
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    activeRequest = controller;
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      return await fetch(url, { ...options, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    } finally {
      clearTimeout(timeout);
      if (activeRequest === controller) activeRequest = null;
    }
  }
  function validate(data) {
    const validCount = (value) => Number.isSafeInteger(value) && value >= 0;
    const validDay = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
    const validMetrics = (value) => value && ["page_views", "downloads", "fallback_clicks"].every((key) => validCount(value[key]));
    if (!data || data.timezone !== "Asia/Shanghai" || !validMetrics(data.today) || !validDay(data.today.date) || !validMetrics(data.total) || !Array.isArray(data.days) || data.days.length > 90 || !data.days.every((day) => validMetrics(day) && validDay(day.date)) || new Set(data.days.map((day) => day.date)).size !== data.days.length || !(data.started_at === null || (typeof data.started_at === "string" && !Number.isNaN(Date.parse(data.started_at))))) throw new Error("invalid-data");
    return { ...data, days: [...data.days].sort((a, b) => a.date.localeCompare(b.date)) };
  }
  function drawChart(days) {
    const host = $("trend-chart");
    host.replaceChildren();
    if (!days.length) { host.hidden = true; return; }
    host.hidden = false;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 900 240");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "页面浏览与安装包下载的每日趋势，完整数值见下方每日明细表。");
    function add(tag, attrs, textValue) {
      const node = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
      if (textValue !== undefined) node.textContent = textValue;
      svg.append(node);
      return node;
    }
    const max = Math.max(2, ...days.flatMap((day) => [day.page_views, day.downloads]));
    const x = (index) => days.length === 1 ? 475 : 66 + index * 814 / (days.length - 1);
    const y = (value) => 196 - value / max * 166;
    [0, .5, 1].forEach((ratio) => {
      add("line", { x1: 66, x2: 880, y1: y(max * ratio), y2: y(max * ratio), class: "chart-grid" });
      add("text", { x: 56, y: y(max * ratio) + 4, "text-anchor": "end" }, number.format(Math.round(max * ratio)));
    });
    ["page_views", "downloads"].forEach((metric) => {
      const className = metric === "page_views" ? "chart-views" : "chart-downloads";
      add("polyline", { points: days.map((day, index) => `${x(index)},${y(day[metric])}`).join(" "), class: className });
      if (days.length === 1) add("circle", { cx: x(0), cy: y(days[0][metric]), r: 4, class: className, fill: "none", "stroke-width": 3 });
    });
    const indices = [...new Set([0, Math.floor((days.length - 1) / 2), days.length - 1])];
    indices.forEach((index) => add("text", { x: x(index), y: 225, "text-anchor": index === 0 && days.length > 1 ? "start" : index === days.length - 1 && days.length > 1 ? "end" : "middle" }, days[index].date.slice(5).replace("-", "/")));
    host.append(svg);
  }
  function render(data) {
    ["today", "total"].forEach((period) => {
      $(period + "-views").textContent = number.format(data[period].page_views);
      $(period + "-downloads").textContent = number.format(data[period].downloads);
      $(period + "-fallback").textContent = number.format(data[period].fallback_clicks);
    });
    $("today-date").textContent = data.today.date;
    const range = data.days.length ? `${data.days[0].date} 至 ${data.days[data.days.length - 1].date}` : "暂无每日记录";
    $("trend-range").textContent = range;
    $("daily-caption").textContent = `${range} · 北京时间`;
    $("daily-rows").replaceChildren();
    [...data.days].reverse().forEach((day) => {
      const row = document.createElement("tr");
      [day.date, number.format(day.page_views), number.format(day.downloads), number.format(day.fallback_clicks)].forEach((value) => {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      });
      $("daily-rows").append(row);
    });
    if (!data.days.length) {
      const row = document.createElement("tr"); const cell = document.createElement("td"); cell.colSpan = 4; cell.textContent = "所选日期内暂无每日记录"; row.append(cell); $("daily-rows").append(row);
    }
    drawChart(data.days);
    $("empty-note").hidden = data.days.some((day) => day.page_views || day.downloads || day.fallback_clicks);
    $("started-note").textContent = data.started_at ? `自 ${time.format(new Date(data.started_at))}（北京时间）启用统计，不补造此前的访问和下载。` : "仅从统计启用起记录，不补造此前的访问和下载。当前尚无统计启用时间记录。";
    $("updated-at").textContent = `最后更新：${time.format(new Date())}`;
    $("data-content").hidden = false;
  }
  async function loadStats(initial = false) {
    if (loading) return;
    const version = ++requestVersion;
    setBusy(true);
    $("data-error").hidden = true;
    status(initial ? "正在检查登录状态…" : "正在读取统计…");
    try {
      const response = await request(`/api/admin/stats?days=${$("day-range").value}`);
      if (version !== requestVersion) return;
      if (response.status === 401) { showLogin(authenticated ? "登录已过期，请重新输入所有者密码。" : "请登录后查看统计。"); return; }
      if (!response.ok) throw new Error("server-error");
      const data = validate(await response.json());
      if (version !== requestVersion) return;
      snapshot = data;
      showDashboard();
      render(data);
      status("统计已更新。");
    } catch (error) {
      if (version !== requestVersion) return;
      const message = error.name === "AbortError" ? "读取超时，请检查网络后重试。" : error.message === "invalid-data" ? "统计数据格式异常，请稍后重试。" : "无法读取统计，请检查网络或稍后重试。";
      if (!authenticated) {
        showLogin(message);
        $("login-error").textContent = message;
        return;
      }
      showDashboard();
      $("data-error-text").textContent = snapshot ? `${message} 下方保留上次成功取得的数据，最后更新时间未改变。` : `${message} 当前没有可显示的数据。`;
      $("data-error").hidden = false;
      status("本次读取失败。");
    } finally { if (version === requestVersion) setBusy(false); }
  }
  $("toggle-password").addEventListener("click", () => {
    const visible = $("owner-key").type === "password";
    $("owner-key").type = visible ? "text" : "password";
    $("toggle-password").textContent = visible ? "隐藏密码" : "显示密码";
    $("toggle-password").setAttribute("aria-pressed", String(visible));
  });
  $("owner-key").addEventListener("compositionstart", () => { composing = true; });
  $("owner-key").addEventListener("compositionend", () => { composing = false; });
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if ($("login-submit").disabled || composing) return;
    const key = $("owner-key").value;
    $("login-error").textContent = "";
    $("owner-key").removeAttribute("aria-invalid");
    if (!key) { $("login-error").textContent = "请输入所有者密码。"; $("owner-key").setAttribute("aria-invalid", "true"); $("owner-key").focus(); return; }
    $("login-submit").disabled = true;
    $("login-form").setAttribute("aria-busy", "true");
    status("正在登录…");
    try {
      const response = await request("/api/admin/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
      if (!response.ok) {
        const message = response.status === 401 || response.status === 403 ? "密码不正确，请重新输入。" : response.status === 429 ? "尝试次数较多，请稍后再登录。" : "登录未完成，请稍后重试。";
        $("login-error").textContent = message; $("owner-key").setAttribute("aria-invalid", "true"); $("owner-key").focus(); status("登录未完成。"); return;
      }
      $("owner-key").value = "";
      $("owner-key").type = "password";
      $("toggle-password").textContent = "显示密码";
      $("toggle-password").setAttribute("aria-pressed", "false");
      showDashboard();
      await loadStats();
      if (authenticated) $("day-range").focus();
    } catch (error) { $("login-error").textContent = error.name === "AbortError" ? "登录请求超时，请重试。" : "无法连接，请检查网络后重试。"; status("登录未完成。"); }
    finally { $("login-submit").disabled = false; $("login-form").setAttribute("aria-busy", "false"); }
  });
  $("logout").addEventListener("click", async () => {
    if ($("logout").disabled) return;
    ++requestVersion;
    if (activeRequest) activeRequest.abort();
    setBusy(true);
    $("logout").disabled = true;
    status("正在退出登录…");
    try {
      const response = await request("/api/admin/session", { method: "DELETE" });
      if (!response.ok && response.status !== 401) throw new Error("logout-failed");
      showLogin("已退出登录。", true);
    } catch { status("退出未完成，请检查网络后再次点击退出登录。"); }
    finally { $("logout").disabled = false; setBusy(false); }
  });
  $("refresh").addEventListener("click", () => loadStats());
  $("retry").addEventListener("click", () => loadStats());
  $("day-range").addEventListener("change", () => { clearData(); loadStats(); });
  $("export-csv").addEventListener("click", () => {
    if (!snapshot || loading) return;
    const lines = [["日期（北京时间）", "页面浏览", "安装包下载", "GitHub备用点击"], ...snapshot.days.map((day) => [day.date, day.page_views, day.downloads, day.fallback_clicks])];
    const blob = new Blob(["\uFEFF", lines.map((row) => row.join(",")).join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `WorkBox-网站统计-${snapshot.today.date}-${$("day-range").value}天.csv`; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    status("每日 CSV 已交给浏览器下载，内容来自当前显示的数据。");
  });
  loadStats(true);
})();
