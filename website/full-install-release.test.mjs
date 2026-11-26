import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const previousR1 = Object.freeze({ version: '1.0.0', tag: 'v1.0.0.20261125.1', edition: '正式安装版',
  date: '2026-11-25', filename: 'WorkBox-1.0.0-Setup-x64-20261125-r1.exe', size: 47961533,
  sha256: 'ad935dd19d3e2717605492d5d1b9d1212aa3895a26a924eb02d534ff5bb5d295' });
const previousSource = '5ececd9e3bef310bf08f9805aa2cd71b70d55e14';

test('11月25日r2稳定清单只提供该构建已发布的完整安装包', async () => {
  const tag = 'v1.0.0.20261125.2';
  const name = 'WorkBox-1.0.0-Setup-x64-20261125-r2.exe';
  const response = await handleRequest(new Request('https://example.com/updates/stable.json'), {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const metadata = await response.json();
  assert.equal(metadata.tag_name, tag);
  assert.equal(metadata.html_url, `https://github.com/232577/workbox/releases/tag/${tag}`);
  assert.equal(metadata.draft, false);
  assert.equal(metadata.prerelease, false);
  assert.equal(release.tag, tag);
  assert.equal(release.filename, name);
  assert.equal(release.size, 135264533);
  assert.equal(release.sha256, '04d5a420c363a8ad19f028ab21e86596c2e7088fe0183a8e952164ee9c21463b');
  assert.match(metadata.body, /来源提交 d337659261024ad3634154f75da26bcb49ac5a2c。/);
  assert.match(metadata.body, /完整安装包/);
  assert.doesNotMatch(metadata.body, /可使用对应的增量包/);
  assert.match(metadata.body, /旧运行库.*可恢复历史/);
  assert.match(metadata.body, /未知文件.*阻止/);
  assert.match(metadata.body, /留白.*滚轮.*日期/);
  assert.match(metadata.body, /日历.*键盘.*粘贴/);
  assert.match(metadata.body, /复制文件.*拖/);
  assert.match(metadata.body, /Ctrl\+C.*项目名/);
  assert.match(metadata.body, /已交付.*加急/);
  assert.deepEqual(metadata.assets, [{
    name, size: 135264533, state: 'uploaded',
    digest: 'sha256:04d5a420c363a8ad19f028ab21e86596c2e7088fe0183a8e952164ee9c21463b',
    browser_download_url: `https://github.com/232577/workbox/releases/download/${tag}/${name}`,
  }]);
});

test('11月25日r1保留原安装包和匹配增量，全部42项旧目录与下载路径保留', async () => {
  const { deltas, ...full } = releases[1];
  assert.deepEqual(full, previousR1);
  assert.equal(deltas.length, 2);
  assert.equal(releases.length, 43);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '18e303c3f29c64e547cf085b7ad03a6876bfd8cd9f50a40ebd1683ef350e5adc');
  for (const old of releases.slice(1)) {
    const response = await handleRequest(new Request(`https://example.com/download/${old.filename}`, { method: 'HEAD' }), {},
      async (url, options) => {
        assert.equal(url, `https://github.com/232577/workbox/releases/download/${old.tag}/${old.filename}`);
        assert.equal(options.method, 'HEAD');
        return new Response(null, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(old.size) } });
      });
    assert.equal(response.status, 200, old.filename);
    assert.equal(response.headers.get('content-length'), String(old.size), old.filename);
  }
});

test('下载页记录新版来源并保留11月23日r1说明及历史媒体归属', async () => {
  const page = documents['/releases.html'];
  assert.match(page, /首次新增日期.*时间段/);
  assert.match(page, /名单窗口.*重新打开.*读屏/);
  const history = page.slice(page.indexOf('<section id="release-history"'));
  const november24 = releases.find(item => item.tag === 'v1.0.0.20261124.2');
  assert.equal(november24.sha256, 'c43afd00f6a9b48698268115651f08a86e444638163ec12071844225dbce44c8');
  assert.ok(history.indexOf(`/download/${previousR1.filename}`) >= 0);
  assert.ok(history.indexOf(`/download/${previousR1.filename}`) < history.indexOf(`/download/${november24.filename}`));
  assert.ok(history.indexOf(`/download/${november24.filename}`) < history.indexOf('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(history.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.match(history, /查看 42 个历史安装包/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const normalizedNotes = notes.replaceAll('\r\n', '\n');
  const r1Heading = '# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-25 r1';
  const startOfHistory = normalizedNotes.indexOf(r1Heading);
  assert.ok(startOfHistory > 0);
  const historicalNotes = normalizedNotes.slice(startOfHistory);
  const r1Notes = historicalNotes.split('\n---\n')[0];
  for (const value of [previousSource, previousR1.tag, previousR1.filename, previousR1.sha256, '47,961,533',
    '8d658584dcdc62cf5bfb7c94d1d0a3a40013db4aad5fbdbb8ed8fcd65f62198e']) assert.ok(r1Notes.includes(value));
  assert.match(r1Notes, /对应的增量包/);
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-24 r2'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-24 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-23 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r2'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-20 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-16 r1'));
  assert.ok(notes.includes('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-13 r2'));
  assert.ok(notes.includes('dfc965b73f2d26e081f16d813e839bf729a79020ee2b5e4ae8b1190210f7d314'));
  assert.equal(createHash('sha256').update(historicalNotes).digest('hex'),
    'ca6c7d2c9cf6cf428ea1752099cb18a90532be6a538bc8bc35a4137a87bc5176');
  assert.match(documents['/guide.html'], /来自 v1\.0\.0\.20261109\.4 的同一隔离实例/);
});
