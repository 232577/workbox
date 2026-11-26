import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releaseAssets, releases } from './worker.mjs';

const source = '711cb675da7423c6ccae77a26a191d744ef54e81';
const tag = 'v1.0.0.20261126.3';
const filename = 'WorkBox-1.0.0-Setup-x64-20261126-r3.exe';
const size = 135321834;
const sha256 = 'ad9de3836fa454497c8aa2f64fe63457dae7a9466492a1cde9077bbc6361eaf7';

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

test('这是家个人交付说明区分本机收件、提交状态和办公室交接', async () => {
  const guide = documents['/guide.html'];
  const personal = guide.match(/<h3 id="personal-site-delivery">([\s\S]*?)<h3/)?.[1];
  assert.ok(personal);
  assert.match(personal, /办公室账号未登录时[^<]*本人材料待办/);
  assert.match(personal, /同名[^<]*编号/);
  assert.match(personal, /拖入[^<]*Word[^<]*待交付/);
  assert.match(personal, /确认交付[^<]*本机归档[^<]*材料完成/);
  assert.match(personal, /Word 原件保留在本机/);
  const releasePage = documents['/releases.html'];
  assert.ok(releasePage.includes('/guide.html#personal-site-delivery'));
  assert.match(releasePage, /预览所选项目/);
  assert.match(releasePage, /NTFS[^<]*命名数据流/);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  const update = metadata.body.split('\n\n')[0];
  assert.match(update, /本人材料待办[^。]*个人项目/);
  assert.match(update, /确认交付[^。]*材料完成/);
  assert.match(update, /NTFS[^。]*命名数据流/);
  assert.match(update, /批量交付[^。]*预览所选项目/);
});

test('本次更新与指南说明精确定位已完成任务、批量状态与删除、NTFS 收录', async () => {
  const guide = documents['/guide.html'];
  const personal = guide.match(/<h3 id="personal-site-delivery">([\s\S]*?)<h3/)?.[1];
  assert.ok(personal);
  assert.match(personal, /无需登录办公室服务/);
  assert.match(personal, /原站页面[^。]*刷新[^。]*「已完成」[^。]*同一编号/);
  const batch = guide.match(/<details id="bulk-project-status">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(batch);
  assert.match(batch, /右击已选项目[^。]*「批量设为待交付」/);
  assert.match(batch, /「批量删除[^。]*「已删除」[^。]*恢复/);
  const update = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const content of [documents['/releases.html'], update.body.split('\n\n')[0]]) {
    assert.match(content, /「已完成」[^。<]*同一编号/);
    assert.match(content, /批量[^。<]*状态[^。<]*删除/);
    assert.match(content, /NTFS[^。<]*收录[^。<]*命名数据流/);
  }
});

test('首页、指南及发布说明的下载入口和校验值绑定同一个 r3 安装包', async () => {
  assert.equal(release.tag, tag);
  assert.equal(release.filename, filename);
  assert.equal(release.size, size);
  assert.equal(release.sha256, sha256);
  assert.equal(release.version, '1.0.0');
  assert.equal(release.edition, '正式安装版');
  assert.equal(release.date, '2026-11-26');
  assert.notEqual(release.sha256, releases[1].sha256, '新安装包不能复用旧摘要');
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
  assert.ok(currentNotes.includes('6d5d5c368221314e73068aa0201db18533f8c926a8c6c996eb8e4f8377a9bdbe'));
  for (const text of [readme, currentNotes]) {
    for (const value of [source, tag, filename, release.sha256, release.size.toLocaleString('en-US')]) assert.ok(text.includes(value), value);
  }
});

test('11月25日 r1 的两份来源增量只保留在历史身份下', () => {
  const previous = releases.find(item => item.tag === 'v1.0.0.20261125.1');
  assert.deepEqual(previous.deltas, expectedDeltas);
  assert.deepEqual(releaseAssets(previous).slice(1), expectedDeltas.map(({ name, size, sha256, url }) => ({
    name, size, state: 'uploaded', digest: `sha256:${sha256}`, browser_download_url: url,
  })));
});

test('11月26日 r3 仅向正式 r2 提供经校验的增量包，完整安装包仍可用', async () => {
  const delta = {
    "from_tag": "v1.0.0.20261126.2",
    "to_tag": "v1.0.0.20261126.3",
    "name": "WorkBox-1.0.0-Delta-x64-20261126-r2-to-20261126-r3.wbdelta",
    "size": 16196928,
    "sha256": "feba1ec742af10e034387a727935a9f94c81b97a83e2be40b799d6ddea55b55d",
    "url": "https://github.com/232577/workbox/releases/download/v1.0.0.20261126.3/WorkBox-1.0.0-Delta-x64-20261126-r2-to-20261126-r3.wbdelta",
    "state": "uploaded"
  };
  const response = await handleRequest(new Request('https://example.com/updates/stable.json'), {});
  const metadata = await response.json();
  assert.deepEqual(release.deltas, [delta]);
  assert.deepEqual(metadata.assets.slice(1), [{
    name: delta.name, size: delta.size, state: 'uploaded',
    digest: `sha256:${delta.sha256}`, browser_download_url: delta.url,
  }]);
  assert.equal(metadata.assets[0].name, filename);
  const last = releases.find(item => item.tag === delta.from_tag);
  assert.deepEqual(releaseAssets(last).map(asset => asset.name), [
    'WorkBox-1.0.0-Setup-x64-20261126-r2.exe',
    'WorkBox-1.0.0-Delta-x64-20261126-r1-to-20261126-r2.wbdelta',
  ]);
  for (const text of [metadata.body.split('\n\n')[0], documents['/guide.html'], documents['/releases.html']]) {
    assert.match(text, /从 11 月 26 日 r2 更新时[^。<]*程序文件校验通过可使用增量包/);
    assert.match(text, /首次安装[^。<]*来源版本不匹配[^。<]*增量不可用[^。<]*完整安装包/);
  }
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const currentNotes = notes.replaceAll('\r\n', '\n').split('\n---\n')[0];
  for (const value of [delta.name, delta.sha256, '16,196,928']) assert.ok(currentNotes.includes(value), value);
});

test('当前摘要、指南和更新源说明键盘确认、批量交付及工作量刷新', async () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.ok(highlights);
  assert.match(highlights, /团队交件/);
  assert.match(highlights, /完整安装包/);
  assert.doesNotMatch(highlights, /可使用对应的增量包/);
  assert.match(page, /构建 v1\.0\.0\.20261125\.1[^<]*从 11 月 24 日 r1 或 11 月 24 日 r2[^<]*对应的增量包/);
  assert.ok(highlights.includes('/guide.html#bulk-project-status'));
  assert.ok(highlights.includes('/guide.html#online-update'));
  assert.match(page, /改名后保留当前批次筛选和搜索条件/);
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
  assert.match(page, /查看 45 个历史安装包/);
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
  assert.match(bulkDelivery, /选中至少两个项目.*批量操作.*弹(?:窗|层|出的操作面板)/);
  assert.match(bulkDelivery, /滚动位置.*分栏宽度/);
  assert.match(bulkDelivery, /「设为已交付…」.*「应用」.*确认批量交付/);
  assert.match(bulkDelivery, /预览所选项目/);
  assert.match(bulkDelivery, /「批量确认交付」.*「仅确认当前项目交付」/);
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
  assert.match(metadata.body, /至少两个项目.*批量操作.*弹(?:窗|层|出的操作面板)/);
  assert.match(metadata.body, /滚动位置.*分栏宽度/);
  assert.match(metadata.body, /选中多个待交付项目后可批量交付/);
  assert.doesNotMatch(metadata.body, /开启[^。]*「批量选择」|开启[^。]*“批量选择”/);
  assert.match(metadata.body, /全部材料校验通过后.*交付记录/);
  assert.match(metadata.body, /点击「今日工作量」.*记录.*计数/);
  assert.match(metadata.body, /改名后仍归原批次，保留当前批次筛选和搜索条件/);
  assert.match(metadata.body, /独立的同盘恢复位置/);
  assert.match(metadata.body, /从高级设置主动检查一次/);
  assert.doesNotMatch(metadata.body, /从窗口底部或高级设置/);
});
