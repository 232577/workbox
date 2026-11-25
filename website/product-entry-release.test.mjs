import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = '5ececd9e3bef310bf08f9805aa2cd71b70d55e14';
const tag = 'v1.0.0.20261125.1';
const filename = 'WorkBox-1.0.0-Setup-x64-20261125-r1.exe';
const size = 47961533;
const sha256 = 'ad935dd19d3e2717605492d5d1b9d1212aa3895a26a924eb02d534ff5bb5d295';

const expectedDeltas = [
  {
    "from_tag": "v1.0.0.20261124.1",
    "to_tag": "v1.0.0.20261125.1",
    "name": "WorkBox-1.0.0-Delta-x64-20261124-r1-to-20261125-r1.wbdelta",
    "size": 15330920,
    "sha256": "916b2cf2a34e632a81c30b49e19b1201ed02276938f00fadc3c590df134ab539",
    "url": "https://github.com/232577/workbox/releases/download/v1.0.0.20261125.1/WorkBox-1.0.0-Delta-x64-20261124-r1-to-20261125-r1.wbdelta",
    "state": "uploaded"
  },
  {
    "from_tag": "v1.0.0.20261124.2",
    "to_tag": "v1.0.0.20261125.1",
    "name": "WorkBox-1.0.0-Delta-x64-20261124-r2-to-20261125-r1.wbdelta",
    "size": 15330917,
    "sha256": "e19feed00bc4f3d3b5c17adcc3fd1e730e7db5c795a28c72ada7bdf5e1bc0e89",
    "url": "https://github.com/232577/workbox/releases/download/v1.0.0.20261125.1/WorkBox-1.0.0-Delta-x64-20261124-r2-to-20261125-r1.wbdelta",
    "state": "uploaded"
  }
];

test('11月25日r1下载与来源增量绑定新构建，全部41项旧目录逐字保留', async () => {
  assert.equal(release.tag, tag);
  assert.deepEqual(release.deltas, expectedDeltas);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-25');
  assert.equal(releases.length, 42);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '40a8d02caa2ce25fc39b2326dec751f3a2f86a13c8373ad4e7800104f4012dbd');
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
  assert.ok(currentNotes.includes('8d658584dcdc62cf5bfb7c94d1d0a3a40013db4aad5fbdbb8ed8fcd65f62198e'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('当前摘要、指南和更新源说明键盘确认、批量交付及工作量刷新', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.ok(highlights.includes('从 11 月 24 日 r1 或 11 月 24 日 r2 更新时，可使用对应的增量包。'));
  assert.match(highlights, /Ctrl 点选.*Shift 连选.*Ctrl\+A 全选当前结果/);
  assert.ok(highlights.includes('/guide.html#bulk-delivery'));
  assert.ok(highlights.includes('/guide.html#rename-project'));
  assert.ok(highlights.includes('/guide.html#online-update'));
  assert.match(highlights, /改名后留在原批次/);
  assert.ok(page.includes('项目右键菜单可直接选择「待开发」「开发中」「待交付」。待交付项目使用独立的「确认交付…」。'));
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
  assert.match(page, /查看 41 个历史安装包/);
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
  assert.match(bulkDelivery, /Ctrl 点选.*Shift 点选.*Ctrl\+A 全选当前结果/);
  assert.match(bulkDelivery, /清单顶部.*「全选」/);
  assert.match(bulkDelivery, /选中至少两个项目.*批量操作.*清单底部.*分栏宽度/);
  assert.match(bulkDelivery, /「设为已交付…」.*「应用」.*确认批量交付/);
  assert.doesNotMatch(bulkDelivery, /(?:开启|勾选)「批量选择」|「多选」/);
  const filtering = guide.match(/<details><summary>项目筛选和操作在哪里？<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.match(filtering, /窄窗单击只选中项目.*「查看文件」.*Enter.*打开详情/);
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
  assert.match(metadata.body, /Ctrl 点选.*Shift 点选.*Ctrl\+A 全选当前结果/);
  assert.match(metadata.body, /至少两个项目.*批量操作.*清单底部.*分栏宽度/);
  assert.match(metadata.body, /选中多个待交付项目后可批量交付/);
  assert.doesNotMatch(metadata.body, /开启[^。]*「批量选择」|开启[^。]*“批量选择”/);
  assert.match(metadata.body, /全部材料校验通过后.*交付记录/);
  assert.match(metadata.body, /点击「今日工作量」.*记录.*计数/);
  assert.match(metadata.body, /改名后仍归原批次，保留当前批次筛选和搜索条件/);
  assert.match(metadata.body, /独立的同盘恢复位置/);
  assert.match(metadata.body, /从高级设置主动检查一次/);
  assert.doesNotMatch(metadata.body, /从窗口底部或高级设置/);
});
