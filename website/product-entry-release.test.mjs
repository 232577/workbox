import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = '72a0db4f5b7ddf73e38f9340cebcd392634b098d';
const tag = 'v1.0.0.20261124.1';
const filename = 'WorkBox-1.0.0-Setup-x64-20261124-r1.exe';
const size = 47966278;
const sha256 = '19cf7076dc2ba0c18dc11d008c938fe6b14adc3c413b3d14769fd6b1309b6872';

test('11月24日r1当前下载与在线更新绑定新构建，全部39项旧目录逐字保留', async () => {
  assert.equal(release.tag, tag);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-24');
  assert.equal(releases.length, 40);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'a56ba70d3c3db88c69b3c469d90dd8356ba14f6ed7fe90375826463bdc0e8097');
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
  assert.ok(currentNotes.includes('c23a090ae173e941fd6e441f636008e777b8e9b4feaa70acca4fa1b803c707cc'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('当前摘要、指南和更新源说明键盘确认、批量交付及工作量刷新', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.equal([...highlights.matchAll(/<li>/g)].length, 3);
  assert.match(highlights, /默认焦点.*确认交付/);
  assert.match(highlights, /Enter 或小键盘 Enter 确认.*Esc 取消/);
  assert.match(highlights, /批量选择.*窗口大小.*分栏宽度/);
  assert.match(highlights, /勾选.*批量交付/);
  assert.match(highlights, /点击「今日工作量」.*记录.*计数/);
  assert.match(page, /下拉列表展开时，Enter 只选择版本/);
  assert.match(page, /全部材料校验通过后.*交付记录/);
  assert.match(page, /复制文件.*拖/);
  assert.match(page, /Ctrl\+C.*项目名/);
  assert.match(page, /已交付.*加急/);
  assert.match(page, /改名后仍归原批次/);
  assert.match(page, /合并.*沿用保留项目的工作目录/);
  assert.match(page, /完整安装包.*原安装目录/);
  assert.match(page, /查看 39 个历史安装包/);
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261123-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261120-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261116-r1.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r2.exe'));
  assert.ok(page.includes('/download/WorkBox-1.0.0-Setup-x64-20261113-r1.exe'));
  assert.doesNotMatch(highlights, /已部署|商业签名|第二台.*已验证/);
  const guide = documents['/guide.html'];
  assert.ok(highlights.includes('/guide.html#delivery-keyboard'));
  assert.ok(highlights.includes('/guide.html#bulk-delivery'));
  assert.ok(highlights.includes('/guide.html#refresh-workload'));
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
