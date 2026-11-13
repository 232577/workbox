import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = 'ff4a336d66adb65c6cfedfb5b258067c703dee69';
const tag = 'v1.0.0.20261113.2';
const filename = 'WorkBox-1.0.0-Setup-x64-20261113-r2.exe';
const size = 40841667;
const sha256 = 'dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314';

test('11月13日r2当前下载与在线更新绑定新构建，全部34项旧目录逐字保留', async () => {
  assert.equal(release.tag, tag);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-13');
  assert.equal(releases.length, 35);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '80e04e51ab583eba097b9edb8727a2c9a2a9d98f90707f470d490d7650c8387b');
  assert.notEqual(release.sha256, releases[1].sha256, '新安装包不能复用旧摘要');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: filename, size: release.size, state: 'uploaded',
    digest: `sha256:${release.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${tag}/${filename}` }]);
  const home = documents['/index.html'];
  assert.equal([...home.matchAll(/class="[^\"]*download-link[^\"]*" href="([^"#]+)"/g)]
    .filter(match => match[1] === `/download/${filename}`).length, 2);
  for (const page of [home, documents['/releases.html']]) {
    for (const value of [filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(page.includes(value), value);
    assert.ok(page.includes(`https://github.com/232577/workbox/releases/download/${tag}/${filename}`));
  }
  assert.ok(documents['/guide.html'].includes(`https://github.com/232577/workbox/releases/download/${tag}/${filename}`));
  const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  assert.ok(currentNotes.includes('3ede384765bb27995b4f59d4cdc1b4bddd63bd3e3dddbdefd16190df3a600765'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('r2更新只说明入口整理，原工作流程和历史下载仍完整可查', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.equal([...highlights.matchAll(/<li>/g)].length, 3);
  assert.match(highlights, /名单核对.*检查更新.*原生菜单/);
  assert.match(highlights, /常用入口.*保留/);
  assert.match(highlights, /原数据.*历史版本.*保留/);
  assert.match(page, /查看 34 个历史安装包/);
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r1.exe'));
  assert.doesNotMatch(highlights, /已部署|商业签名|第二台.*已验证/);
  const guide = documents['/guide.html'];
  assert.match(guide, /从“高级设置 → 检查更新”主动检查一次/);
  assert.match(guide, /“高级设置”集中提供[^<]*名单核对/);
  assert.doesNotMatch(guide, /从窗口底部或“高级设置 → 检查更新”/);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.match(metadata.body, /从高级设置主动检查一次/);
  assert.doesNotMatch(metadata.body, /从窗口底部或高级设置/);
});
