import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(value).digest('hex');
const source = '621d7ddc13416f396fc8483c8a6d63f212ae49af';

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

test('11月9日r2下载和稳定清单绑定通过核验的同一构建', async () => {
  assert.deepEqual(release, { version: '1.0.0', tag: 'v1.0.0.20261109.2', edition: '正式安装版',
    date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r2.exe', size: 40639450,
    sha256: '4d6525f5c8fe336a6c81d67248cb37ae2f0a355337991a54392cc8a30acffa43' });
  assert.equal(releases.length, 24);
  const response = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  assert.equal(response.status, 200);
  const metadata = await response.json();
  assert.equal(metadata.tag_name, release.tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.match(metadata.body, /待开发.*导出名单.*“项目名称”一列/);
  assert.match(metadata.body, /窄窗.*返回项目清单/);
  assert.match(html, /正式安装版 · 2026 年 11 月 9 日/);
  assert.match(html, /<a class="download-link" href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261109-r1\.exe">历史正式安装版/);
});

test('首页两张原始PNG绑定r2同一已安装EXE及来源提交', async () => {
  const executable = 'f2b1613c42e31867e19ec35e704ab6b057b3c197f4ad7342102a35256f86864d';
  assert.equal(registry.latest_screenshot_exe_sha256, executable);
  assert.equal(Object.keys(registry.files).length, 56);
  for (const [name, width, height, size, sha256] of [
    ['workbox-pending-export-light-20261109-r2.png', 1102, 792, 60154, 'd55e3dbeed7d777c30c39525b32e60d76edc2ef66d3fd4939ee57c5801da0668'],
    ['workbox-pending-export-narrow-dark-20261109-r2.png', 662, 512, 35501, '440067c3763096e4e235586a08f343c16b4bd0d533fea80b1de68d82f6df4501'],
  ]) {
    const path = `public/media/${name}`;
    const facts = registry.files[path];
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(bytes.readUInt32BE(16), width);
    assert.equal(bytes.readUInt32BE(20), height);
    assert.equal(bytes.length, size);
    assert.equal(digest(bytes), sha256);
    assert.equal(facts.size, size);
    assert.equal(facts.sha256, sha256);
    assert.equal(facts.source_commit, source);
    assert.equal(facts.source_exe_sha256, executable);
    assert.ok(html.includes(`href="/media/${name}"`));
    assert.ok(html.includes(`src="/media/${name}" width="${width}" height="${height}"`));
  }
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
