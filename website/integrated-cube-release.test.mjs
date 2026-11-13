import { archivedReadmes } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

test('r4 历史目录保持且当前更新保留品牌和恢复修复说明', async () => {
  assert.deepEqual(releases.find(item => item.tag === 'v1.0.0.20261112.4'), {version: '1.0.0', tag: 'v1.0.0.20261112.4', edition: '正式安装版', date: '2026-11-12',
    filename: 'WorkBox-1.0.0-Setup-x64-20261112-r4.exe', size: 39415647, sha256: 'bf45029cab5b7a67b0c88deba0df0a5514ee5482e265fd8b5d83340c938013ed'});
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, release.tag);
  assert.ok(metadata.body.includes('来源提交 ff4a336d66adb65c6cfedfb5b258067c703dee69。'));
  for (const text of [metadata.body, archivedReadmes.product,
    await readFile(new URL('./public/releases.html', import.meta.url), 'utf8')]) {
    for (const feature of ['恢复阻断', '嵌套 ZIP', '桌面队列', '48×48', '32、40、48、56、64']) assert.ok(text.includes(feature));
  }
});

test('新发布不覆盖原 r3 安装包身份和下载入口', async () => {
  const old = releases.find(item => item.tag === 'v1.0.0.20261112.3');
  assert.equal(old.filename, 'WorkBox-1.0.0-Setup-x64-20261112-r3.exe');
  assert.equal(old.size, 39405750);
  assert.equal(old.sha256, '1865219068cebf810d47e5da074cef21585a6cce193025247af40abf94c88ee8');
  const page = await readFile(new URL('./public/releases.html', import.meta.url), 'utf8');
  const history = page.slice(page.indexOf('<section id="release-history"'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261112-r3.exe'));
});
