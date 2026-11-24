import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const current = Object.freeze({ version: '1.0.0', tag: 'v1.0.0.20261124.1', edition: '正式安装版',
  date: '2026-11-24', filename: 'WorkBox-1.0.0-Setup-x64-20261124-r1.exe', size: 47966278,
  sha256: '19cf7076dc2ba0c18dc11d008c938fe6b14adc3c413b3d14769fd6b1309b6872' });
const source = '72a0db4f5b7ddf73e38f9340cebcd392634b098d';

test('11月24日r1使用完整安装包，原39项下载目录逐字保留', async () => {
  assert.deepEqual(release, current);
  assert.equal(releases.length, 40);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'a56ba70d3c3db88c69b3c469d90dd8356ba14f6ed7fe90375826463bdc0e8097');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, current.tag);
  assert.equal(metadata.draft, false);
  assert.equal(metadata.prerelease, false);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: current.filename, size: current.size, state: 'uploaded',
    digest: `sha256:${current.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${current.tag}/${current.filename}` }]);
  assert.match(metadata.body, /旧运行库.*可恢复历史/);
  assert.match(metadata.body, /未知文件.*阻止/);
  assert.match(metadata.body, /留白.*滚轮.*日期/);
  assert.match(metadata.body, /日历.*键盘.*粘贴/);
  assert.match(metadata.body, /复制文件.*拖/);
  assert.match(metadata.body, /Ctrl\+C.*项目名/);
  assert.match(metadata.body, /已交付.*加急/);
  assert.match(metadata.body, /本次升级仅提供完整安装包/);
});

test('下载页记录新版来源并保留11月23日r1说明及历史媒体归属', async () => {
  const page = documents['/releases.html'];
  assert.match(page, /首次新增日期.*时间段/);
  assert.match(page, /名单窗口.*重新打开.*读屏/);
  const history = page.slice(page.indexOf('<section id="release-history"'));
  assert.equal(releases[1].tag, 'v1.0.0.20261123.1');
  assert.equal(releases[1].sha256, '0074fb031707d619bc294cdff12454f8e8dc8dfbca66ae184b97dff88d894dfa');
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) >= 0);
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) < history.indexOf('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.match(history, /查看 39 个历史安装包/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  for (const value of [source, current.tag, current.filename, current.sha256, '47,966,278',
    'c23a090ae173e941fd6e441f636008e777b8e9b4feaa70acca4fa1b803c707cc']) assert.ok(currentNotes.includes(value));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-23 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r2'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-16 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-13 r2'));
  assert.ok(notes.includes('dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314'));
  const historicalNotes = notes.replaceAll('\r\n', '\n').split('\n---\n\n').slice(1).join('\n---\n\n');
  assert.equal(createHash('sha256').update(historicalNotes).digest('hex'),
    '1638d4d49c5d680c349e66e34fc44a3464ad14e78d4659c458155d06522d1f16');
  assert.match(documents['/guide.html'], /来自 v1\.0\.0\.20261109\.4 的同一隔离实例/);
});
