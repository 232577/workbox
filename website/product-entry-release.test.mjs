import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = '14c793ca45ae687fd26ea32ba9aa304575e3fe9e';
const tag = 'v1.0.0.20261124.2';
const filename = 'WorkBox-1.0.0-Setup-x64-20261124-r2.exe';
const size = 47964803;
const sha256 = 'c43afd00f6a9b48698268115651f08a86e444638163ec12071844225dbce44c8';

const expectedDeltas = [
  {
    "from_tag": "v1.0.0.20261123.1",
    "to_tag": "v1.0.0.20261124.2",
    "name": "WorkBox-1.0.0-Delta-x64-20261123-r1-to-20261124-r2.wbdelta",
    "size": 15329267,
    "sha256": "e39f08e6a837b47f4745532fcd308d8dcb7611362cfd4515aed7c2c5f779733f",
    "url": "https://github.com/232577/workbox/releases/download/v1.0.0.20261124.2/WorkBox-1.0.0-Delta-x64-20261123-r1-to-20261124-r2.wbdelta",
    "state": "uploaded"
  },
  {
    "from_tag": "v1.0.0.20261124.1",
    "to_tag": "v1.0.0.20261124.2",
    "name": "WorkBox-1.0.0-Delta-x64-20261124-r1-to-20261124-r2.wbdelta",
    "size": 15329268,
    "sha256": "1a9ba822ad338ed43dfb99d3ea7a84df6f1cbcaf71cfdab61b40b4931cf76265",
    "url": "https://github.com/232577/workbox/releases/download/v1.0.0.20261124.2/WorkBox-1.0.0-Delta-x64-20261124-r1-to-20261124-r2.wbdelta",
    "state": "uploaded"
  }
];

test('11月24日r2下载与两条增量绑定新构建，全部40项旧目录逐字保留', async () => {
  assert.equal(release.tag, tag);
  assert.deepEqual(release.deltas, expectedDeltas);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-24');
  assert.equal(releases.length, 41);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '6a5f6b2fc95e4a146c1ac3e69b2a73e7a2215b35142a5691a61d34e460f5b97b');
  assert.notEqual(release.sha256, releases[1].sha256, '新安装包不能复用旧摘要');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: filename, size: release.size, state: 'uploaded',
    digest: `sha256:${release.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${tag}/${filename}` },
    ...expectedDeltas.map(({ name, size, sha256, url }) => ({ name, size, state: 'uploaded',
      digest: `sha256:${sha256}`, browser_download_url: url }))]);
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
  assert.ok(currentNotes.includes('967d0687bfea480e85b34dcbbf179750a6eeb07148dc38b7a2cd8540fba3fa16'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('当前摘要、指南和更新源说明键盘确认、批量交付及工作量刷新', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.match(highlights, /11 月 23 日 r1 和 11 月 24 日 r1 各有对应的增量包/);
  assert.ok(highlights.includes('/guide.html#online-update'));
  assert.ok(highlights.includes('项目右键菜单可直接选择「待开发」「开发中」「待交付」。待交付项目使用独立的「确认交付…」。'));
  assert.ok(highlights.includes('/guide.html#project-status-menu'));
  assert.equal([...highlights.matchAll(/<li>/g)].length, 3);
  assert.match(page, /默认焦点.*确认交付/);
  assert.match(page, /Enter 或小键盘 Enter 确认.*Esc 取消/);
  assert.match(page, /批量选择.*窗口大小.*分栏宽度/);
  assert.match(page, /勾选.*批量交付/);
  assert.match(page, /点击「今日工作量」.*记录.*计数/);
  assert.match(page, /下拉列表展开时，Enter 只选择版本/);
  assert.match(page, /全部材料校验通过后.*交付记录/);
  assert.match(page, /复制文件.*拖/);
  assert.match(page, /Ctrl\+C.*项目名/);
  assert.match(page, /已交付.*加急/);
  assert.match(page, /改名后仍归原批次/);
  assert.match(page, /合并.*沿用保留项目的工作目录/);
  assert.match(page, /完整安装包.*原安装目录/);
  assert.match(page, /查看 40 个历史安装包/);
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261123-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r1.exe'));
  assert.doesNotMatch(highlights, /已部署|商业签名|第二台.*已验证/);
  const guide = documents['/guide.html'];
  assert.ok(guide.includes('项目右键菜单可直接选择「待开发」「开发中」「待交付」。待交付项目使用独立的「确认交付…」。'));
  const delivery = guide.match(/<details id="delivery-keyboard">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(delivery);
  assert.match(delivery, /默认焦点.*确认交付/);
  assert.match(delivery, /Enter 或小键盘 Enter 确认.*Esc 取消/);
  assert.match(delivery, /Tab.*取消.*Enter.*取消/);
  assert.match(delivery, /下拉列表展开时，Enter 只选择版本/);
  assert.match(delivery, /待核对.*ZIP.*不能确认/);
  const bulkDelivery = guide.match(/<details id="bulk-delivery">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(bulkDelivery);
  assert.match(bulkDelivery, /批量选择.*窗口大小.*分栏宽度/);
  assert.match(bulkDelivery, /勾选.*批量交付/);
  assert.match(bulkDelivery, /「多选」.*「设为已交付…」.*「应用到所选项目」/);
  assert.match(bulkDelivery, /全部材料校验通过后.*交付记录/);
  const workload = guide.match(/<details id="refresh-workload">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(workload);
  assert.match(workload, /点击「今日工作量」.*记录.*计数/);
  assert.match(workload, /保留已选日期和搜索条件/);
  assert.match(guide, /id="copy-files"[^>]*>[\s\S]*?文件操作 → 复制文件[\s\S]*?项目清单选中项目后按 Ctrl\+C/);
  assert.match(guide, /id="rename-project"[^>]*>[\s\S]*?独立的同盘恢复位置/);
  assert.match(guide, /从“高级设置 → 检查更新”主动检查一次/);
  assert.match(guide, /“高级设置”集中提供[^<]*名单核对/);
  assert.doesNotMatch(guide, /从窗口底部或“高级设置 → 检查更新”/);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.match(metadata.body, /默认焦点.*确认交付/);
  assert.match(metadata.body, /Enter 或小键盘 Enter 确认.*Esc 取消/);
  assert.match(metadata.body, /下拉列表展开时，Enter 只选择版本/);
  assert.match(metadata.body, /批量选择.*窗口大小.*分栏宽度/);
  assert.match(metadata.body, /勾选.*批量交付/);
  assert.match(metadata.body, /全部材料校验通过后.*交付记录/);
  assert.match(metadata.body, /点击「今日工作量」.*记录.*计数/);
  assert.match(metadata.body, /改名后仍归原批次。/);
  assert.match(metadata.body, /独立的同盘恢复位置/);
  assert.match(metadata.body, /从高级设置主动检查一次/);
  assert.doesNotMatch(metadata.body, /从窗口底部或高级设置/);
});
