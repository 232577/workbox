import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(value).digest('hex');

test('待开发名单入口、导出范围和工作簿内容有明确说明', () => {
  const details = html.match(/<details\b[^>]*id="pending-project-export"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.match(details, /选择“待开发”.*“导出名单”/);
  assert.match(details, /全部未归档、未删除的待开发项目/);
  assert.match(details, /搜索、批次和其他筛选只影响当前清单/);
  assert.match(details, /只有“项目名称”一列.*每个项目一行/);
  assert.match(details, /没有待开发项目时.*表头/);
});

test('宽窗并排和窄窗单页详情属于同一项目工作台', () => {
  const workbench = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(workbench, /宽窗并排显示清单和详情/);
  assert.match(workbench, /窄窗点击项目打开详情.*返回项目清单.*原阅读位置/);
});

test('保留11月9日r1及以前23个下载记录和54项原始媒体', async () => {
  const previousRelease = releases.findIndex(item => item.tag === 'v1.0.0.20261109.1');
  assert.ok(previousRelease >= 0);
  const previous = releases.slice(previousRelease);
  assert.equal(previous.length, 23);
  assert.equal(digest(JSON.stringify(previous)), '0713e3d2b5d03754e1ec51be4e6492576769603395c4311e23d087c1e974fae2');
  for (const { filename } of previous) assert.ok(html.includes(`/download/${filename}`), filename);

  const media = Object.entries(registry.files);
  const previousMedia = media.findIndex(([path]) => path === 'public/media/workbox-batch-progress-light-20261109-r1.png');
  assert.ok(previousMedia >= 0);
  const historical = Object.fromEntries(media.slice(previousMedia));
  assert.equal(Object.keys(historical).length, 54);
  assert.equal(digest(JSON.stringify(historical)), '1aa2cf5f56f796359d4da5d1900da2cbb0129a1a74f7dc395f241e7dd67458a1');
  for (const [path, facts] of Object.entries(historical)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, facts.size, path);
    assert.equal(digest(bytes), facts.sha256, path);
  }
});
