import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const current = Object.freeze({ version: '1.0.0', tag: 'v1.0.0.20261125.1', edition: '正式安装版',
  date: '2026-11-25', filename: 'WorkBox-1.0.0-Setup-x64-20261125-r1.exe', size: 47961533,
  sha256: 'ad935dd19d3e2717605492d5d1b9d1212aa3895a26a924eb02d534ff5bb5d295' });
const source = '5ececd9e3bef310bf08f9805aa2cd71b70d55e14';

test('11月25日r1提供完整安装包和匹配增量，原41项下载目录逐字保留', async () => {
  const { deltas, ...full } = release;
  assert.deepEqual(full, current);
  assert.equal(deltas.length, 2);
  assert.equal(releases.length, 42);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '40a8d02caa2ce25fc39b2326dec751f3a2f86a13c8373ad4e7800104f4012dbd');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, current.tag);
  assert.equal(metadata.draft, false);
  assert.equal(metadata.prerelease, false);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets[0], { name: current.filename, size: current.size, state: 'uploaded',
    digest: `sha256:${current.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${current.tag}/${current.filename}` });
  assert.match(metadata.body, /旧运行库.*可恢复历史/);
  assert.match(metadata.body, /未知文件.*阻止/);
  assert.match(metadata.body, /留白.*滚轮.*日期/);
  assert.match(metadata.body, /日历.*键盘.*粘贴/);
  assert.match(metadata.body, /复制文件.*拖/);
  assert.match(metadata.body, /Ctrl\+C.*项目名/);
  assert.match(metadata.body, /已交付.*加急/);
  assert.match(metadata.body, /对应的增量包/);
  assert.doesNotMatch(metadata.body, /本次升级仅提供完整安装包/);
});

test('下载页记录新版来源并保留11月23日r1说明及历史媒体归属', async () => {
  const page = documents['/releases.html'];
  assert.match(page, /首次新增日期.*时间段/);
  assert.match(page, /名单窗口.*重新打开.*读屏/);
  const history = page.slice(page.indexOf('<section id="release-history"'));
  assert.equal(releases[1].tag, 'v1.0.0.20261124.2');
  assert.equal(releases[1].sha256, 'c43afd00f6a9b48698268115651f08a86e444638163ec12071844225dbce44c8');
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) >= 0);
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) < history.indexOf('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.match(history, /查看 41 个历史安装包/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  for (const value of [source, current.tag, current.filename, current.sha256, '47,961,533',
    '8d658584dcdc62cf5bfb7c94d1d0a3a40013db4aad5fbdbb8ed8fcd65f62198e']) assert.ok(currentNotes.includes(value));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-24 r2'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-24 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-23 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r2'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-16 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-13 r2'));
  assert.ok(notes.includes('dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314'));
  const historicalNotes = notes.replaceAll('\r\n', '\n').split('\n---\n\n').slice(1).join('\n---\n\n');
  assert.equal(createHash('sha256').update(historicalNotes).digest('hex'),
    '0f42d25330fb7499e9e7452b8c398719719fb92a5a950e5de26dd8bdb245f4ab');
  assert.match(documents['/guide.html'], /来自 v1\.0\.0\.20261109\.4 的同一隔离实例/);
});
