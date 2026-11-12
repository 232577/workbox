import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

test('立方品牌 r3 安装包、源码与更新清单使用实际已核验身份', async () => {
  assert.deepEqual(release, {
    version: '1.0.0', tag: 'v1.0.0.20261112.3', edition: '正式安装版', date: '2026-11-12',
    filename: 'WorkBox-1.0.0-Setup-x64-20261112-r3.exe', size: 39405750,
    sha256: '1865219068cebf810d47e5da074cef21585a6cce193025247af40abf94c88ee8',
  });
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, release.tag);
  assert.ok(metadata.body.includes('来源提交 284dda8773712e249ecd7edd741b7dcc9765b9dc。'));
  for (const text of [metadata.body,
    await readFile(new URL('../README.md', import.meta.url), 'utf8'),
    await readFile(new URL('./README.md', import.meta.url), 'utf8'),
    await readFile(new URL('./public/releases.html', import.meta.url), 'utf8')]) {
    assert.match(text, /48×48 逻辑像素/);
    assert.match(text, /32、40、48、56、64/);
    assert.match(text, /重启保留尺寸/);
  }
});

test('已发布 r2 的资产身份和历史下载路径保留不变', async () => {
  assert.deepEqual(releases.find(item => item.tag === 'v1.0.0.20261112.2'), {
    version: '1.0.0', tag: 'v1.0.0.20261112.2', edition: '正式安装版', date: '2026-11-12',
    filename: 'WorkBox-1.0.0-Setup-x64-20261112-r2.exe', size: 42280123,
    sha256: '719baaa47e3ba7feeb29d0574565785a15880a03242ba5a9ac1e86832837ec23',
  });
  const page = await readFile(new URL('./public/releases.html', import.meta.url), 'utf8');
  const history = page.slice(page.indexOf('<section id="release-history"'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261112-r2.exe'));
});
