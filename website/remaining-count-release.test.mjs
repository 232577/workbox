import { siteHtml, documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = siteHtml;
const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
const siteReadme = await readFile(new URL('./README.md', import.meta.url), 'utf8');
const releaseNotes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
const source = '8b09af66f20932eb305fd0272ea648cf8dfc42a1';
const expected = { version: '1.0.0', tag: 'v1.0.0.20261112.4', edition: '正式安装版',
  date: '2026-11-12', filename: 'WorkBox-1.0.0-Setup-x64-20261112-r4.exe', size: 39415647,
  sha256: 'bf45029cab5b7a67b0c88deba0df0a5514ee5482e265fd8b5d83340c938013ed' };
// The r2-era snapshots remain byte-exact; r4 and r3 have independent identity tests.
const historicalStart = releases.findIndex(item => item.tag === 'v1.0.0.20261112.2');
assert.ok(historicalStart >= 0, '历史 r2 目录必须存在');
const previousReleases = releases.slice(historicalStart);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

test('11月12日r2批量加急使用独立安装包，并保留全部30项历史目录和原发布说明', async () => {
  assert.equal(previousReleases[0].tag, 'v1.0.0.20261112.2');
  assert.equal(previousReleases.length, 31);
  assert.equal(digest(JSON.stringify(previousReleases.slice(1))), '38c5d65bb495491167cd5bd25c53a349d0ccdf04cdf8a8161f45459bf59f80ec');
  const allNotes = releaseNotes.replaceAll('\r\n', '\n');
  const notes = allNotes.slice(allNotes.indexOf('# WorkBox 工作箱 1.0.0 正式安装版 · 2026-11-12 r2'));
  assert.equal(digest(notes.slice(notes.indexOf('\n---\n\n') + '\n---\n\n'.length)),
    '688fd97b2e9091977073929ad1c03000f6539bb8c7fefbb954d1a819e3b5a002');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /批量选择.*已选项目.*右键菜单/);
    assert.match(text, /标记加急.*取消加急/);
    assert.match(text, /打开菜单时的选择/);
    assert.match(text, /单选和右击未选项目仍只处理当前项目/);
  }
  assert.match(documents['/releases.html'], /href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261112-r1\.exe">历史正式安装版/);
});

test('指南明确区分所选项目批量加急与当前项目操作，不增加首页可见说明', () => {
  const guide = documents['/guide.html'].match(/<details\b[^>]*id="bulk-urgency"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.match(guide, /批量选择.*已选项目.*右击/);
  assert.match(guide, /所选 N 个项目.*标记加急.*取消加急/);
  assert.match(guide, /当前项目.*其他操作只作用于当前项目/);
  assert.match(guide, /500.*已归档.*已删除/);
  assert.doesNotMatch(documents['/index.html'], /所选 N 个项目|打开菜单时的选择/);
});

test('11月12日r1收件身份修复独立发布，并逐字保留此前全部29项下载目录', async () => {
  assert.equal(previousReleases[1].tag, 'v1.0.0.20261112.1');
  assert.equal(previousReleases.length, 31);
  assert.equal(digest(JSON.stringify(previousReleases.slice(2))), '4f3c58620c320c0978bf067fcc21e87dcc6c74ccdb735ad3f4297f2504c757d7');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /项目列表支持 APP 名称/);
    assert.match(text, /下划线后的任意附注/);
    assert.match(text, /APP 前的空白和大小写/);
    assert.match(text, /新项目名称不会按任意附注自动裁剪/);
    assert.match(text, /保留原显示名、编号和工作目录/);
    assert.match(text, /不自动合并/);
  }
  assert.match(documents['/releases.html'], /href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261111-r1\.exe">历史正式安装版/);
});

test('11月12日r4目录、首页与稳定清单使用同一准确安装包和桌面来源', async () => {
  assert.deepEqual(release, expected);
  for (const value of [expected.filename, expected.sha256, source]) assert.ok(releaseNotes.includes(value));
  assert.ok(releaseNotes.includes('7760e997ded567dd5c1aec4435f023b82b64664ae757e249568999aac4f690fc'));
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, expected.tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: expected.filename, size: expected.size, state: 'uploaded',
    digest: `sha256:${expected.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${expected.tag}/${expected.filename}` }]);
  for (const text of [html, readme, siteReadme]) {
    for (const value of [expected.filename, expected.sha256, source, '39,415,647']) assert.ok(text.includes(value), value);
  }
  const primary = [...documents['/index.html'].matchAll(/class="[^"]*download-link[^"]*" href="([^"#]+)"/g)].map(match => match[1]);
  assert.equal(primary.filter(value => value === `/download/${expected.filename}`).length, 2);
  assert.match(documents['/index.html'], /Windows 10[^<]*11[^<]*x64[^<]*39\.4 MB/);
  assert.ok(html.includes(`href="https://github.com/232577/workbox/releases/download/${expected.tag}/${expected.filename}"`));
  for (const text of [html, readme, siteReadme, metadata.body]) {
    assert.match(text, /Studio/);
    assert.match(text, /窗口位置.*最大化/);
    assert.match(text, /全局快捷键默认关闭/);
    assert.match(text, /批量选择项目时减少重复读取/);
    assert.match(text, /读取失败后清单与详情保持同步/);
    assert.match(text, /项目搜索最多 1000 个字符/);
    assert.match(text, /退出时保存设置或升级时备份数据库失败后，已启用的全局快捷键仍可使用/);
    assert.doesNotMatch(text, /r1[^。<\n]*(?:第二设备验证通过|物理键盘验证通过|全部原生界面验证通过)/);
  }
  for (const text of [readme, siteReadme]) assert.doesNotMatch(text, /待发布/);
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
  const download = documents['/releases.html'];
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
  const backup = documents['/guide.html'].match(/<p>升级前请备份([\s\S]*?)<\/p>/)?.[1];
  assert.match(backup, /全部已登记历史文稿目录.*原工作位置/);
  const migration = html.match(/<details><summary>下载失败，或换一台电脑使用怎么办？<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.match(migration, /备份并迁移[^。]*已登记历史文稿目录/);
});

test('11月12日r2保留全部30历史路由、r7与更早目录和原58媒体字节及归属', async () => {
  assert.equal(previousReleases.length, 31);
  assert.equal(digest(JSON.stringify(previousReleases.slice(3))), '47dc35631d6353174b5dc167db1ae11b7826284a2215ecb4ffbc3d79e6f0c3eb');
  assert.deepEqual(previousReleases[3], {version:'1.0.0',tag:'v1.0.0.20261109.7',edition:'正式安装版',
    date:'2026-11-09',filename:'WorkBox-1.0.0-Setup-x64-20261109-r7.exe',size:42226464,
    sha256:'240a816e8c5049e028060994002a5907ba59c58762ac6ff10c89eb4ce8f2d855'});
  assert.equal(digest(JSON.stringify(previousReleases.slice(4))), '2a2393cc3bf6a086d80998e6f767efca3a1a17f32610311ce948f95947cb3871');
  assert.equal(previousReleases[4].tag, 'v1.0.0.20261109.6');
  assert.equal(digest(JSON.stringify(previousReleases.slice(5))), 'a67157fa9bac8f292ff8b1a84a116e86f58edf3880565217d0e06d94f34925d1');
  assert.equal(digest(JSON.stringify(previousReleases.slice(6))), '6227a923fd6c12b67dd9311e09e1266ea077f7d22557b9cb18a3d373b02d07ff');
  for (const item of previousReleases.slice(3)) assert.ok(html.includes(`/download/${item.filename}`));
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
  assert.ok(!releases.some(item => ['v1.0.0.20261110.1','v1.0.0.20261110.2','v1.0.0.20261110.3','v1.0.0.20261110.4'].includes(item.tag)));
  assert.doesNotMatch(html, /WorkBox-1\.0\.0-Setup-x64-20261110-r[1-4]\.exe/);
});

test('沿用r4实拍准确标注历史构建，不把既有照片改称新版', () => {
  const section = documents['/guide.html'];
  assert.match(section, /v1\.0\.0\.20261109\.4/);
  assert.match(section, /2432b7e237db6424bd70e01f9a96f7567c5e644e/);
  assert.match(section, /r7.*数量.*日期.*规则/);
  assert.doesNotMatch(section, /r[567]\.png|v1\.0\.0\.20261109\.[567][^<]*实拍/);
});
