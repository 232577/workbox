import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(value).digest('hex');
const r2Source = '621d7ddc13416f396fc8483c8a6d63f212ae49af';
const r4Source = '2432b7e237db6424bd70e01f9a96f7567c5e644e';

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

test('新版横向工作台说明与导航和两种导出按钮一致', () => {
  const workbench = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(workbench, /顶部横向导航.*“项目”.*“今日工作量”.*“高级设置”/);
  assert.match(workbench, /搜索和“筛选”在清单上方/);
  assert.match(workbench, /点击“导出全部”可获取全部交付表/);
  assert.match(html, /从窗口底部或“高级设置 → 检查更新”主动检查一次/);
});

test('11月9日r4历史下载保持原安装包与构建，稳定清单继续提供横向工作台说明', async () => {
  const r4 = releases.find(item => item.tag === 'v1.0.0.20261109.4');
  assert.deepEqual(r4, { version: '1.0.0', tag: 'v1.0.0.20261109.4', edition: '正式安装版',
    date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r4.exe', size: 40662142,
    sha256: '745931fb42979eda910f76a4bbf0a9462c9d32e95679b1776d2a5b4274cd4220' });
  const response = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  assert.equal(response.status, 200);
  const metadata = await response.json();
  assert.equal(metadata.tag_name, release.tag);
  assert.match(metadata.body, /横向导航.*今日工作量/);
  assert.match(html, /<a class="download-link" href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261109-r2\.exe">历史正式安装版/);
});

test('首页两张横向工作台原始PNG绑定r4同一已安装EXE', async () => {
  const executable = '92292a7787d252ebe5c3b814ad6f28ef3b9042ac21a661a631af65660395f579';
  assert.equal(registry.latest_screenshot_exe_sha256, executable);
  assert.equal(Object.keys(registry.files).length, 58);
  for (const [name, width, height, size, sha256] of [
    ['workbox-pending-export-light-20261109-r4.png', 1102, 792, 57306, '8364d23eb147dde24e4fc53b8de340b886d58524ac8166eea3c39cf09b57b38a'],
    ['workbox-pending-export-narrow-dark-20261109-r4.png', 662, 512, 35748, '9e9a880c3e588c8f082f32f99f0695b942b05c865ed095f0fb6db999d4767b77'],
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
    assert.equal(facts.source_commit, r4Source);
    assert.equal(facts.source_exe_sha256, executable);
    assert.ok(html.includes(`href="/media/${name}"`));
    assert.ok(html.includes(`src="/media/${name}" width="${width}" height="${height}"`));
  }
});

test('11月9日r2目录和下载继续绑定原安装包，稳定清单保留导出说明', async () => {
  const r2 = releases.find(item => item.tag === 'v1.0.0.20261109.2');
  assert.deepEqual(r2, { version: '1.0.0', tag: 'v1.0.0.20261109.2', edition: '正式安装版',
    date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r2.exe', size: 40639450,
    sha256: '4d6525f5c8fe336a6c81d67248cb37ae2f0a355337991a54392cc8a30acffa43' });
  const old = await handleRequest(new Request(`https://workbox.ltxiangzi.dpdns.org/download/${r2.filename}`, {method: 'HEAD'}), {},
    async (target, {method}) => {
      assert.equal(target, `https://github.com/232577/workbox/releases/download/${r2.tag}/${r2.filename}`);
      assert.equal(method, 'HEAD');
      return new Response(null, { headers: {'Content-Type': 'application/octet-stream', 'Content-Length': String(r2.size)} });
    });
  assert.equal(old.status, 200);
  assert.equal(old.headers.get('Content-Length'), String(r2.size));
  const response = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  assert.equal(response.status, 200);
  const metadata = await response.json();
  assert.equal(metadata.tag_name, release.tag);
  assert.match(metadata.body, /待开发.*导出名单.*“项目名称”一列/);
  assert.match(metadata.body, /窄窗.*返回项目清单/);
  assert.match(html, /正式安装版 · 2026 年 11 月 9 日/);
  assert.match(html, /<a class="download-link" href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261109-r1\.exe">历史正式安装版/);
});

test('11月9日r2原始PNG保留同一历史安装版EXE来源', async () => {
  const executable = 'f2b1613c42e31867e19ec35e704ab6b057b3c197f4ad7342102a35256f86864d';
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
    assert.equal(facts.source_commit, r2Source);
    assert.equal(facts.source_exe_sha256, executable);
  }
});

test('保留11月9日r2及以前24个下载记录和56项原始媒体，不公开r3', async () => {
  const previousRelease = releases.findIndex(item => item.tag === 'v1.0.0.20261109.2');
  assert.ok(previousRelease >= 0);
  const previous = releases.slice(previousRelease);
  assert.equal(previous.length, 24);
  assert.equal(digest(JSON.stringify(previous)), 'cd545e9c020b1d4490f7bc2fc484bac387616c8edbe5729e23d0f717c9d03987');
  for (const { filename } of previous) assert.ok(html.includes(`/download/${filename}`), filename);
  assert.ok(!releases.some(item => item.tag === 'v1.0.0.20261109.3'));
  assert.doesNotMatch(html, /WorkBox-1\.0\.0-Setup-x64-20261109-r3\.exe/);

  const media = Object.entries(registry.files);
  const previousMedia = media.findIndex(([path]) => path === 'public/media/workbox-pending-export-light-20261109-r2.png');
  assert.ok(previousMedia >= 0);
  const historical = Object.fromEntries(media.slice(previousMedia));
  assert.equal(Object.keys(historical).length, 56);
  assert.equal(digest(JSON.stringify(historical)), '2a1d62b905976000d034a22f9417e73b1be8f85a4b8fa378589916610c00870f');
  for (const [path, facts] of Object.entries(historical)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, facts.size, path);
    assert.equal(digest(bytes), facts.sha256, path);
  }
});
