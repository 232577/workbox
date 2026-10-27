const DAY_MS = 86400000;
const SHANGHAI_OFFSET = 8 * 60 * 60 * 1000;
const MAX_BODY_BYTES = 1024;
const TYPES = Object.freeze({
  page_view: 'page_views',
  download_start: 'downloads',
  fallback_click: 'fallback_clicks',
});
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const dayOf = (at) => new Date(at + SHANGHAI_OFFSET).toISOString().slice(0, 10);
const zero = () => ({ page_views: 0, downloads: 0, fallback_clicks: 0 });
const json = (value, status = 200) => Response.json(value, {
  status, headers: { 'cache-control': 'no-store' },
});

async function readEvent(request) {
  const declared = request.headers.get('content-length');
  if (declared !== null && Number(declared) > MAX_BODY_BYTES) return { status: 413 };
  if (!request.body) return { status: 400 };
  const reader = request.body.getReader();
  let size = 0;
  const chunks = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return { status: 413 };
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!value || Array.isArray(value) || typeof value !== 'object'
      || Object.keys(value).some((key) => !['id', 'type', 'at'].includes(key))
      || typeof value.type !== 'string' || !Object.hasOwn(TYPES, value.type)
      || typeof value.id !== 'string' || !UUID.test(value.id)) return { status: 400 };
    const at = value.at === undefined ? Date.now() : value.at;
    if (!Number.isSafeInteger(at) || at < 0 || at > 253402271999999) return { status: 400 };
    return { value: { id: value.id.toLowerCase(), type: value.type, at } };
  } catch {
    return { status: 400 };
  } finally {
    reader.releaseLock();
  }
}

// 仅供 Worker 的 Durable Object 绑定调用，不绑定公开 HTTP 路由。
export class SiteMetrics {
  constructor(state, env) {
    this.storage = state.storage;
    this.sql = state.storage.sql;
    this.storage.transactionSync(() => {
      this.sql.exec(`CREATE TABLE IF NOT EXISTS metrics_daily (
        day TEXT PRIMARY KEY,
        page_views INTEGER NOT NULL DEFAULT 0,
        downloads INTEGER NOT NULL DEFAULT 0,
        fallback_clicks INTEGER NOT NULL DEFAULT 0
      )`);
      this.sql.exec(`CREATE TABLE IF NOT EXISTS metrics_seen (
        id TEXT NOT NULL,
        type TEXT NOT NULL,
        day TEXT NOT NULL,
        PRIMARY KEY (id, type)
      )`);
      this.sql.exec('CREATE INDEX IF NOT EXISTS metrics_seen_day ON metrics_seen(day)');
      this.sql.exec(`CREATE TABLE IF NOT EXISTS metrics_metadata (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
      )`);
    });
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === '/event') {
      if (request.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
      const parsed = await readEvent(request);
      if (parsed.status) return json({ error: parsed.status === 413 ? 'body_too_large' : 'invalid_event' }, parsed.status);
      return json({ ok: true, counted: this.record(parsed.value) });
    }
    if (url.pathname === '/summary') {
      if (request.method !== 'GET') return json({ error: 'method_not_allowed' }, 405);
      const days = url.searchParams.get('days') ?? '7';
      if (!['7', '30', '90'].includes(days) || url.searchParams.getAll('days').length > 1) {
        return json({ error: 'invalid_days' }, 400);
      }
      return json(this.summary(Number(days)));
    }
    return json({ error: 'not_found' }, 404);
  }

  record({ id, type, at }) {
    const day = dayOf(at);
    const cutoff = dayOf(Date.now() - 29 * DAY_MS);
    // 去重、每日累加和开始时间共用事务；失败时整体回滚。
    return this.storage.transactionSync(() => {
      this.sql.exec('DELETE FROM metrics_seen WHERE day < ?', cutoff);
      const inserted = this.sql.exec(
        'INSERT OR IGNORE INTO metrics_seen (id, type, day) VALUES (?, ?, ?) RETURNING id',
        id, type, day,
      ).toArray();
      if (!inserted.length) return false;
      const column = TYPES[type];
      this.sql.exec(`INSERT INTO metrics_daily (day, ${column}) VALUES (?, 1)
        ON CONFLICT(day) DO UPDATE SET ${column} = ${column} + 1`, day);
      const started = new Date(at).toISOString();
      this.sql.exec(`INSERT INTO metrics_metadata (key, value) VALUES ('started_at', ?)
        ON CONFLICT(key) DO UPDATE SET value = MIN(value, excluded.value)`, started);
      return true;
    });
  }

  summary(count) {
    const now = Date.now();
    const today = dayOf(now);
    const dates = Array.from({ length: count }, (_, index) => dayOf(now - (count - 1 - index) * DAY_MS));
    return this.storage.transactionSync(() => {
      // 无事件时也能清理过期匿名键；聚合计数不参与清理。
      this.sql.exec('DELETE FROM metrics_seen WHERE day < ?', dayOf(now - 29 * DAY_MS));
      const rows = this.sql.exec(`SELECT day AS date, page_views, downloads, fallback_clicks
        FROM metrics_daily WHERE day >= ? AND day <= ? ORDER BY day`, dates[0], today).toArray();
      const byDate = new Map(rows.map((row) => [row.date, row]));
      const days = dates.map((date) => byDate.get(date) ?? { date, ...zero() });
      const total = this.sql.exec(`SELECT COALESCE(SUM(page_views), 0) AS page_views,
        COALESCE(SUM(downloads), 0) AS downloads,
        COALESCE(SUM(fallback_clicks), 0) AS fallback_clicks FROM metrics_daily`).one();
      const metadata = this.sql.exec("SELECT value FROM metrics_metadata WHERE key = 'started_at'").toArray();
      return {
        timezone: 'Asia/Shanghai',
        started_at: metadata[0]?.value ?? null,
        today: days.at(-1),
        total,
        days,
        definitions: {
          page_views: '页面加载后提交的浏览事件次数，不代表独立访客人数。',
          downloads: '下载响应成功开始发送的次数，不代表文件已完整下载或安装。',
          fallback_clicks: '备用下载链接点击次数，不代表文件已下载。',
          deduplication: '同一随机事件 ID 和类型在最近 30 个北京时间日历日内只计一次；过期后可重新计入。',
          retention: '匿名去重键保留最近 30 个北京时间日历日，每日汇总长期保留。',
        },
      };
    });
  }
}
