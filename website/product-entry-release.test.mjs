import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = '166ed85be98f169fbd5bde03a4d12ef9847324f6';
const tag = 'v1.0.0.20261123.1';
const filename = 'WorkBox-1.0.0-Setup-x64-20261123-r1.exe';
const size = 47963748;
const sha256 = '0074fb031707d619bc294cdff12454f8e8dc8dfbca66ae184b97dff88d894dfa';

test('11月23日r1当前下载与在线更新绑定新构建，全部38项旧目录逐字保留', async () => {
  assert.equal(release.tag, tag);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-23');
  assert.equal(releases.length, 39);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'a821c7a393f7758f4f25e66d1c7115506b8ace3bb046945b6d1f771ad7e46ad5');
  assert.notEqual(release.sha256, releases[1].sha256, '新安装包不能复用旧摘要');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: filename, size: release.size, state: 'uploaded',
    digest: `sha256:${release.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${tag}/${filename}` }]);
  const home = documents['/index.html'];
  assert.equal([...home.matchAll(/class="[^\"]*download-link[^\"]*" href="([^"#]+)"/g)]
    .filter(match => match[1] === `/download/${filename}`).length, 1);
  for (const page of [home, documents['/releases.html']]) {
    for (const value of [filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(page.includes(value), value);
    assert.ok(page.includes(`https://github.com/232577/workbox/releases/download/${tag}/${filename}`));
  }
  assert.ok(documents['/guide.html'].includes(`https://github.com/232577/workbox/releases/download/${tag}/${filename}`));
  const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  assert.ok(currentNotes.includes('4c7a17234c52e094c64d0021302b5db240064f341ecc276ac3dadb27d68563ae'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('当前摘要涵盖复制与拖动、已交付加急显示和项目改名合并', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.equal([...highlights.matchAll(/<li>/g)].length, 3);
  assert.match(highlights, /复制文件.*拖/);
  assert.match(highlights, /Ctrl\+C.*项目名/);
  assert.match(highlights, /已交付.*加急/);
  assert.match(highlights, /改名后仍归原批次/);
  assert.match(highlights, /合并.*沿用保留项目的工作目录/);
  assert.match(page, /完整安装包.*原安装目录/);
  assert.match(page, /查看 38 个历史安装包/);
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r1.exe'));
  assert.doesNotMatch(highlights, /已部署|商业签名|第二台.*已验证/);
  const guide = documents['/guide.html'];
  assert.match(guide, /id="copy-files"[^>]*>[\s\S]*?文件操作 → 复制文件[\s\S]*?项目清单选中项目后按 Ctrl\+C/);
  assert.match(guide, /id="rename-project"[^>]*>[\s\S]*?独立的同盘恢复位置/);
  assert.match(guide, /从“高级设置 → 检查更新”主动检查一次/);
  assert.match(guide, /“高级设置”集中提供[^<]*名单核对/);
  assert.doesNotMatch(guide, /从窗口底部或“高级设置 → 检查更新”/);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.match(metadata.body, /改名后仍归原批次。/);
  assert.match(metadata.body, /独立的同盘恢复位置/);
  assert.match(metadata.body, /从高级设置主动检查一次/);
  assert.doesNotMatch(metadata.body, /从窗口底部或高级设置/);
});
