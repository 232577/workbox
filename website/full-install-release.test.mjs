import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const current = Object.freeze({ version: '1.0.0', tag: 'v1.0.0.20261116.1', edition: '正式安装版',
  date: '2026-11-16', filename: 'WorkBox-1.0.0-Setup-x64-20261116-r1.exe', size: 47442596,
  sha256: 'fcc0ba390b4d057be8f42eb4cb9b0516fc7d694a4051777f1e90b18dc73e0694' });
const source = 'afe99d53107346a24751bb923fffa7692f385ada';

test('已验证的旧库修复包成为唯一当前版本，原35项下载目录逐字保留', async () => {
  assert.deepEqual(release, current);
  assert.equal(releases.length, 36);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '62bbfe64de458639f475ef11a8715d3a4b8d8b4c6ae5b3ce6e935e72c01f6adf');
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
  assert.match(metadata.body, /完整安装包.*不提供.*增量包/);
});

test('下载页说明本次日期与读屏修复，旧r2说明及媒体归属仍保留', async () => {
  const page = documents['/releases.html'];
  assert.match(page, /首次新增日期.*时间段/);
  assert.match(page, /名单窗口.*重新打开.*读屏/);
  assert.ok(page.includes(`/download/${releases[1].filename}`));
  assert.match(page, /查看 35 个历史安装包/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  for (const value of [source, current.tag, current.filename, current.sha256, '47,442,596',
    '13e0a7bf3800605ec3fad3376cc36a2e03ada4bb6531fb679dbbfe04cc348ca4']) assert.ok(currentNotes.includes(value));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-13 r2'));
  assert.ok(notes.includes('dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314'));
  const historicalNotes = notes.replaceAll('\r\n', '\n').split('\n---\n\n').slice(1).join('\n---\n\n');
  assert.equal(createHash('sha256').update(historicalNotes).digest('hex'),
    '38a77be1a4f5ddb9d017781462eb6d8d09c2d8f9dd06a882ea7da32c1bc494a6');
  assert.match(documents['/guide.html'], /来自 v1\.0\.0\.20261109\.4 的同一隔离实例/);
});
