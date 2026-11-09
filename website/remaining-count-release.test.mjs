import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
const siteReadme = await readFile(new URL('./README.md', import.meta.url), 'utf8');
const source = '781f3784e830dc3114e1145b31b6170a729e7aa7';
const expected = { version: '1.0.0', tag: 'v1.0.0.20261109.7', edition: '正式安装版',
  date: '2026-11-09', filename: 'WorkBox-1.0.0-Setup-x64-20261109-r7.exe', size: 42226464,
  sha256: '240a816e8c5049e028060994002a5907ba59c58762ac6ff10c89eb4ce8f2d855' };
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

test('r7目录、首页与稳定清单使用同一准确安装包和桌面来源', async () => {
  assert.deepEqual(release, expected);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, expected.tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: expected.filename, size: expected.size, state: 'uploaded',
    digest: `sha256:${expected.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${expected.tag}/${expected.filename}` }]);
  for (const text of [html, readme, siteReadme]) {
    for (const value of [expected.filename, expected.sha256, source, '42,226,464']) assert.ok(text.includes(value), value);
  }
  const primary = [...html.matchAll(/class="[^"]*download-link[^"]*" href="([^"#]+)"/g)].map(match => match[1]);
  assert.equal(primary.filter(value => value === `/download/${expected.filename}`).length, 2);
  assert.match(html, /<p class="wb-meta">Windows 10 \/ 11 · x64 · 42\.2 MB<\/p>/);
  assert.match(html, /href="https:\/\/github\.com\/232577\/workbox\/releases\/download\/v1\.0\.0\.20261109\.7\/WorkBox-1\.0\.0-Setup-x64-20261109-r7\.exe"/);
});

test('r6准确说明多项目性能修复，不把性能说明冒称实拍或全面验收', async () => {
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /r6 优化项目较多时的搜索、筛选和项目切换/);
    assert.match(text, /减少重复刷新与 ZIP 列表查询/);
    assert.doesNotMatch(text, /r6[^。<\n]*(?:全部原生界面验证通过|第二设备验证通过)/);
  }
});

test('r7待完成显示零与总数，日期和旧稿规则在各入口一致', async () => {
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /待完成4，共5/);
    assert.match(text, /待完成0，共1/);
    assert.match(text, /待开发和开发中/);
    assert.match(text, /首次新增日期/);
    assert.match(text, /当前文件.*旧稿.*独立/);
    assert.match(text, /已有旧稿.*明确选择/);
    assert.match(text, /归档只包含当前文件/);
  }
  assert.doesNotMatch(html, /余2”表示还有 2 个项目未交付/);
  const download = html.match(/<section\b[^>]*id="download"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(download, /待完成4，共5.*待完成0，共1.*待完成只计待开发和开发中/);
  assert.doesNotMatch(download, /余N|r5 修正批次余量/);
  const filters = html.match(/<details><summary>项目筛选和操作在哪里？<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.match(filters, /首次新增日期.*批次下方.*常驻/);
  assert.doesNotMatch(filters, /展开筛选区[^。]*首次新增日期/);
  for (const text of [html, readme, siteReadme]) {
    assert.match(text, /(?:备份|迁移)[^。\n]*已登记历史文稿目录/);
    assert.match(text, /已登记历史文稿目录[^。\n]*原工作位置/);
    assert.match(text, /迁移后核验路径与历史文件可读性/);
  }
  const backup = html.match(/<li>升级前备份([\s\S]*?)<\/li>/)?.[1];
  assert.match(backup, /全部已登记历史文稿目录.*原工作位置/);
  const migration = html.match(/<details><summary>下载失败，或换一台电脑使用怎么办？<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.match(migration, /备份并迁移[^。]*已登记历史文稿目录/);
});

test('r7保留全部27历史路由、r6以前26项与原58媒体字节及归属', async () => {
  assert.equal(releases.length, 28);
  assert.equal(digest(JSON.stringify(releases.slice(1))), '2a2393cc3bf6a086d80998e6f767efca3a1a17f32610311ce948f95947cb3871');
  assert.equal(releases[1].tag, 'v1.0.0.20261109.6');
  assert.equal(digest(JSON.stringify(releases.slice(2))), 'a67157fa9bac8f292ff8b1a84a116e86f58edf3880565217d0e06d94f34925d1');
  assert.equal(digest(JSON.stringify(releases.slice(3))), '6227a923fd6c12b67dd9311e09e1266ea077f7d22557b9cb18a3d373b02d07ff');
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

test('沿用r4实拍准确标注历史构建，不把既有照片改称新版', () => {
  const section = html.match(/<section\b[^>]*id="actual-ui"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(section, /v1\.0\.0\.20261109\.4/);
  assert.match(section, /2432b7e237db6424bd70e01f9a96f7567c5e644e/);
  assert.match(section, /r7.*数量.*日期.*文字说明/);
  assert.doesNotMatch(section, /r[567]\.png|v1\.0\.0\.20261109\.[567][^<]*实拍/);
});
