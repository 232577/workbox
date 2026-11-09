import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
const siteReadme = await readFile(new URL('./README.md', import.meta.url), 'utf8');
const source = '14cf3fb70e22d20a11c28f65925d7f68977b4733';
const expected = { version: '1.0.0', tag: 'v1.0.0.20261109.5', edition: '正式安装版',
  date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r5.exe', size: 42195578,
  sha256: 'cebcb7550a040df1b6b594491188b17525f9bfa11ee8dd1ada54e2467ccdc6ba' };
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

test('r5目录、首页与稳定清单使用同一准确安装包和桌面来源', async () => {
  assert.deepEqual(release, expected);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, expected.tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: expected.filename, size: expected.size, state: 'uploaded',
    digest: `sha256:${expected.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${expected.tag}/${expected.filename}` }]);
  for (const text of [html, readme, siteReadme]) {
    for (const value of [expected.filename, expected.sha256, source, '42,195,578']) assert.ok(text.includes(value), value);
  }
  const primary = [...html.matchAll(/class="[^"]*download-link[^"]*" href="([^"#]+)"/g)].map(match => match[1]);
  assert.equal(primary.filter(value => value === `/download/${expected.filename}`).length, 2);
  assert.match(html, /href="https:\/\/github\.com\/232577\/workbox\/releases\/download\/v1\.0\.0\.20261109\.5\/WorkBox-1\.0\.0-Setup-x64-20261109-r5\.exe"/);
});

test('r5剩余口径明确减去待交付并区分待交付标记与已齐', async () => {
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /剩余＝总数－已交付－待交付/);
    assert.match(text, /待开发和开发中/);
    assert.match(text, /剩余为零.*待交付.*待交付N/);
    assert.match(text, /全部交付.*已齐/);
    assert.match(text, /已交付3\/10.*待交付2.*剩余5/);
  }
  assert.doesNotMatch(html, /余2”表示还有 2 个项目未交付/);
});

test('r5只新增目录首项，保留全部25历史路由与原58媒体字节及归属', async () => {
  assert.equal(releases.length, 26);
  assert.equal(digest(JSON.stringify(releases.slice(1))), '6227a923fd6c12b67dd9311e09e1266ea077f7d22557b9cb18a3d373b02d07ff');
  for (const item of releases.slice(1)) assert.ok(html.includes(`/download/${item.filename}`));
  assert.match(html, /href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261109-r4\.exe">历史正式安装版/);
  const raw = await readFile(new URL('./MEDIA.json', import.meta.url));
  assert.equal(digest(raw), 'e69a2afc0b26271772fff0c5ad1e5f0f933c001a0df3709557ca31ad3d4bf93f');
  const media = JSON.parse(raw);
  assert.equal(Object.keys(media.files).length, 58);
  for (const [path, facts] of Object.entries(media.files)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, facts.size, path); assert.equal(digest(bytes), facts.sha256, path);
  }
  assert.ok(!releases.some(item => item.tag === 'v1.0.0.20261109.3'));
});

test('沿用r4实拍准确标注历史构建，不把既有照片改称r5', () => {
  const section = html.match(/<section\b[^>]*id="actual-ui"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(section, /v1\.0\.0\.20261109\.4/);
  assert.match(section, /2432b7e237db6424bd70e01f9a96f7567c5e644e/);
  assert.match(section, /r5.*数量规则.*文字说明/);
  assert.doesNotMatch(section, /r5\.png|v1\.0\.0\.20261109\.5[^<]*实拍/);
});
