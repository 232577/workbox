import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(value).digest('hex');
const source = '67fab09d4e5392018eb2ae23e67f8573f1fd19c3';

test('批次余量按整批当前状态，筛选和个人工作量不影响数量', () => {
  const batch = html.match(/<section\b[^>]*id="project-batches"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(batch, /待完成4，共5/);
  assert.match(batch, /待完成0，共1/);
  assert.match(batch, /选择批次.*待交付.*已交付/);
  assert.match(batch, /待完成只计待开发和开发中/);
  assert.match(batch, /整批项目的当前状态/);
  assert.match(batch, /搜索或筛选只改变清单.*个人工作量单独统计/);
  assert.match(batch, /归档项目仍计入.*删除.*恢复/);
});

test('已安装用户可主动检查一次，再使用更新并重启', () => {
  const download = html.match(/<section\b[^>]*id="download"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(download, /高级设置 → 检查更新/);
  assert.match(download, /检查一次.*“更新并重启”/);
  assert.match(download, /校验安装包.*备份数据库.*重新打开/);
  assert.match(download, /id="update-recovery"/);
});

test('11月9日r1目录和下载继续绑定原安装包，稳定清单保留批次说明', async () => {
  const r1 = releases.find(item => item.tag === 'v1.0.0.20261109.1');
  assert.deepEqual(r1, { version: '1.0.0', tag: 'v1.0.0.20261109.1', edition: '正式安装版',
    date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r1.exe', size: 40624926,
    sha256: 'a8a7f9ac597900b52adacb6f1a8188d25309b88abc5c40c0af77bad473727524' });
  const old = await handleRequest(new Request(`https://workbox.ltxiangzi.dpdns.org/download/${r1.filename}`, {method: 'HEAD'}), {},
    async (target, {method}) => {
      assert.equal(target, `https://github.com/232577/workbox/releases/download/${r1.tag}/${r1.filename}`);
      assert.equal(method, 'HEAD');
      return new Response(null, { headers: {'Content-Type': 'application/octet-stream', 'Content-Length': String(r1.size)} });
    });
  assert.equal(old.status, 200);
  assert.equal(old.headers.get('Content-Length'), String(r1.size));
  const response = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  const metadata = await response.json();
  assert.equal(response.status, 200);
  assert.match(metadata.body, /待完成4，共5.*待完成0，共1.*整批项目的当前状态.*个人工作量单独统计/);
  assert.match(metadata.body, /检查一次.*更新并重启/);
  assert.match(html, /正式安装版 · 2026 年 11 月 9 日/);
  assert.match(html, /<a class="download-link" href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261106-r1\.exe">历史正式安装版/);
});

test('11月9日r1原始PNG保留同一历史安装版EXE来源', async () => {
  const executable = '713d67114a9f090388f0230247ccbff237b2c80ac51dbfd2a3ede3657070eeff';
  for (const [name, width, height, size, sha256] of [
    ['workbox-batch-progress-light-20261109-r1.png', 1102, 792, 61679, 'c5b62748a80544ed11102ae79793d0caa82a54c3c3d9c01d4372145323ce735d'],
    ['workbox-batch-progress-narrow-dark-20261109-r1.png', 662, 512, 45102, '49e42c0cd1b510a44315c94e7aa67b3562719cfba57c6d0814e93d04df201c44'],
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
    assert.equal(facts.source_exe_sha256, executable);
    assert.equal(facts.source_commit, source);
  }
});

test('保留11月6日r1及以前22个下载记录和52项原始媒体', async () => {
  const previousRelease = releases.findIndex(item => item.tag === 'v1.0.0.20261106.1');
  assert.ok(previousRelease >= 0);
  const previous = releases.slice(previousRelease);
  assert.equal(previous.length, 22);
  assert.equal(digest(JSON.stringify(previous)), '60e7664cb68e72506ef9061f7558b5a5d5533508dc4538662c215b2941d914bb');
  for (const { filename } of previous) assert.ok(html.includes(`/download/${filename}`), filename);

  const media = Object.entries(registry.files);
  const previousMedia = media.findIndex(([path]) => path === 'public/media/workbox-ui-cleanup-light-20261106-r1.png');
  assert.ok(previousMedia >= 0);
  const oldMedia = Object.fromEntries(media.slice(previousMedia));
  assert.equal(Object.keys(oldMedia).length, 52);
  assert.equal(digest(JSON.stringify(oldMedia)), '8124f897178e7d4215259a8182068c356799b44b7a629573b056d5fb536c0fac');
  for (const [path, facts] of Object.entries(oldMedia)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, facts.size, path);
    assert.equal(digest(bytes), facts.sha256, path);
  }
});
