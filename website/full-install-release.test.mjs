import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const current = Object.freeze({ version: '1.0.0', tag: 'v1.0.0.20261120.2', edition: '正式安装版',
  date: '2026-11-20', filename: 'WorkBox-1.0.0-Setup-x64-20261120-r2.exe', size: 47955414,
  sha256: '01b3524ed03da757b46312c4496b4c6ea5804a3da96ba9e19c49b7cf96638803' });
const source = 'f1a398be813905df8505cd2fefbf1ef6c22cf23d';

test('11月20日r2使用完整安装包，原37项下载目录逐字保留', async () => {
  assert.deepEqual(release, current);
  assert.equal(releases.length, 38);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'a897762e95d9399cddb45c041afb1b0d9868f919251c71ea4417f1f66e1da6ee');
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
  assert.match(metadata.body, /本次升级仅提供完整安装包/);
});

test('下载页记录新版来源并保留11月20日r1说明及历史媒体归属', async () => {
  const page = documents['/releases.html'];
  assert.match(page, /首次新增日期.*时间段/);
  assert.match(page, /名单窗口.*重新打开.*读屏/);
  const history = page.slice(page.indexOf('<section id="release-history"'));
  assert.equal(releases[1].tag, 'v1.0.0.20261120.1');
  assert.equal(releases[1].sha256, '8b347731061a9f1a81585c2296a9cf756e4c62eeb53dc887c7005e144e8b0366');
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) >= 0);
  assert.ok(history.indexOf(`/download/${releases[1].filename}`) < history.indexOf('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.match(history, /查看 37 个历史安装包/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  for (const value of [source, current.tag, current.filename, current.sha256, '47,955,414',
    'fabea27d9a2d2835193c1429ed88c090dbb5543b88442046a77911dd5fd18531']) assert.ok(currentNotes.includes(value));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-16 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-13 r2'));
  assert.ok(notes.includes('dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314'));
  const historicalNotes = notes.replaceAll('\r\n', '\n').split('\n---\n\n').slice(1).join('\n---\n\n');
  assert.equal(createHash('sha256').update(historicalNotes).digest('hex'),
    'f97653691680807d461b66753e49ae404b0f3a4c28cc71410bab5d4055627b6b');
  assert.match(documents['/guide.html'], /来自 v1\.0\.0\.20261109\.4 的同一隔离实例/);
});
