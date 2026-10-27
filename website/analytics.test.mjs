import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { SiteMetrics } from './analytics.mjs';

// 使用真正的 SQLite，只适配 Durable Object 同步存储接口。
function fixture(path = ':memory:') {
  const db = new DatabaseSync(path);
  const storage = {
    sql: {
      exec(query, ...bindings) {
        const statement = db.prepare(query);
        const rows = statement.all(...bindings).map((row) => ({ ...row }));
        return {
          toArray: () => rows,
          one() {
            assert.equal(rows.length, 1);
            return rows[0];
          },
          [Symbol.iterator]: () => rows[Symbol.iterator](),
        };
      },
    },
    transactionSync(callback) {
      db.exec('BEGIN IMMEDIATE');
      try {
        const result = callback();
        db.exec('COMMIT');
        return result;
      } catch (error) {
        db.exec('ROLLBACK');
        throw error;
      }
    },
  };
  return { db, storage, object: new SiteMetrics({ storage }, {}) };
}

const fixedNow = Date.parse('2026-10-27T04:00:00Z');
const event = (object, value) => object.fetch(new Request('https://metrics.internal/event', {
  method: 'POST', body: JSON.stringify(value),
}));
const summary = async (object, days = 7) => {
  const response = await object.fetch(new Request(`https://metrics.internal/summary?days=${days}`));
  assert.equal(response.status, 200);
  return response.json();
};

test('空数据只返回真实零值、北京时间窗口与空开始时间', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const { object, db } = fixture();
  t.after(() => db.close());
  const result = await summary(object);
  assert.equal(result.timezone, 'Asia/Shanghai');
  assert.equal(result.started_at, null);
  assert.deepEqual(result.today, { date: '2026-10-27', page_views: 0, downloads: 0, fallback_clicks: 0 });
  assert.deepEqual(result.total, { page_views: 0, downloads: 0, fallback_clicks: 0 });
  assert.equal(result.days.length, 7);
  assert.equal(result.days[0].date, '2026-10-21');
  assert.deepEqual(result.days.at(-1), result.today);
  for (const days of [30, 90]) assert.equal((await summary(object, days)).days.length, days);
});

test('北京时间午夜跨日，id+type 跨日期去重，三类事件分别汇总', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const { object, db } = fixture();
  t.after(() => db.close());
  const id = randomUUID();
  const before = Date.parse('2026-10-26T15:59:59Z');
  const after = Date.parse('2026-10-26T16:00:00Z');
  assert.deepEqual(await (await event(object, { id, type: 'page_view', at: before })).json(), { ok: true, counted: true });
  assert.deepEqual(await (await event(object, { id: id.toUpperCase(), type: 'page_view', at: after })).json(), { ok: true, counted: false });
  await event(object, { id, type: 'download_start', at: after });
  await event(object, { id, type: 'fallback_click', at: after });
  const result = await summary(object);
  assert.equal(result.started_at, '2026-10-26T15:59:59.000Z');
  assert.deepEqual(result.days.at(-2), { date: '2026-10-26', page_views: 1, downloads: 0, fallback_clicks: 0 });
  assert.deepEqual(result.today, { date: '2026-10-27', page_views: 0, downloads: 1, fallback_clicks: 1 });
  assert.deepEqual(result.total, { page_views: 1, downloads: 1, fallback_clicks: 1 });
});

test('默认服务端时间，并发请求不丢计数且重复重试只计一次', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const { object, db } = fixture();
  t.after(() => db.close());
  const ids = Array.from({ length: 100 }, () => randomUUID());
  const responses = await Promise.all(ids.flatMap((id) => [
    event(object, { id, type: 'page_view' }), event(object, { id, type: 'page_view' }),
  ]));
  assert.ok(responses.every((response) => response.status === 200));
  const bodies = await Promise.all(responses.map((response) => response.json()));
  assert.equal(bodies.filter((body) => body.counted).length, 100);
  const result = await summary(object);
  assert.equal(result.total.page_views, 100);
  assert.equal(result.today.page_views, 100);
  assert.equal(result.started_at, new Date(fixedNow).toISOString());
});

test('重新打开文件数据库后汇总和去重状态仍存在，事件表不保存访客资料', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const directory = mkdtempSync(join(import.meta.dirname, '.metrics-test-'));
  let second;
  t.after(() => {
    second?.db.close();
    rmSync(directory, { recursive: true, force: true });
  });
  const path = join(directory, 'metrics.sqlite');
  const first = fixture(path);
  const id = randomUUID();
  await event(first.object, { id, type: 'download_start' });
  first.db.close();
  second = fixture(path);
  assert.equal((await summary(second.object)).total.downloads, 1);
  assert.equal((await (await event(second.object, { id, type: 'download_start' })).json()).counted, false);
  const columns = second.db.prepare('PRAGMA table_info(metrics_seen)').all().map((row) => row.name);
  assert.deepEqual(columns, ['id', 'type', 'day']);
});

test('去重键保留最近30个北京时间日历日，汇总和开始时间长期保留', async (t) => {
  let now = fixedNow;
  t.mock.method(Date, 'now', () => now);
  const { object, db } = fixture();
  t.after(() => db.close());
  const id = randomUUID();
  await event(object, { id, type: 'page_view' });
  now += 29 * 86400000;
  assert.equal((await (await event(object, { id, type: 'page_view' })).json()).counted, false);
  now += 86400000;
  assert.equal((await (await event(object, { id, type: 'page_view' })).json()).counted, true);
  const result = await summary(object, 90);
  assert.equal(result.total.page_views, 2);
  assert.equal(result.started_at, new Date(fixedNow).toISOString());
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM metrics_seen').get().count, 1);
});

test('写入中途失败时去重和汇总一起回滚，重试可以正常计入', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const { object, storage, db } = fixture();
  t.after(() => db.close());
  const exec = storage.sql.exec;
  let fail = true;
  storage.sql.exec = (query, ...bindings) => {
    if (fail && /INSERT INTO metrics_daily/.test(query)) throw new Error('injected failure');
    return exec(query, ...bindings);
  };
  const value = { id: randomUUID(), type: 'page_view' };
  await assert.rejects(event(object, value), /injected failure/);
  assert.equal((await summary(object)).started_at, null);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM metrics_seen').get().count, 0);
  fail = false;
  assert.equal((await (await event(object, value)).json()).counted, true);
  assert.equal((await summary(object)).total.page_views, 1);
});

test('坏输入、超大正文、错误路径和方法均拒绝，不写统计', async (t) => {
  t.mock.method(Date, 'now', () => fixedNow);
  const { object, db } = fixture();
  t.after(() => db.close());
  for (const value of [null, [], {}, { id: randomUUID(), type: 'unknown' },
    { id: randomUUID(), type: ['page_view'] },
    { id: 'bad', type: 'page_view' }, { id: randomUUID(), type: 'page_view', at: 'today' },
    { id: randomUUID(), type: 'page_view', at: -1 },
    { id: randomUUID(), type: 'page_view', ip: '127.0.0.1' }]) {
    assert.equal((await event(object, value)).status, 400);
  }
  assert.equal((await object.fetch(new Request('https://metrics.internal/event', { method: 'POST', body: '{' }))).status, 400);
  assert.equal((await object.fetch(new Request('https://metrics.internal/event', { method: 'POST', body: 'x'.repeat(1025) }))).status, 413);
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('x'.repeat(1025))); controller.close(); } });
  assert.equal((await object.fetch(new Request('https://metrics.internal/event', { method: 'POST', body: stream, duplex: 'half', headers: { 'content-length': '1' } }))).status, 413);
  assert.equal((await object.fetch(new Request('https://metrics.internal/summary?days=8'))).status, 400);
  assert.equal((await object.fetch(new Request('https://metrics.internal/event'))).status, 405);
  assert.equal((await object.fetch(new Request('https://metrics.internal/other'))).status, 404);
  assert.equal((await summary(object)).started_at, null);
});
