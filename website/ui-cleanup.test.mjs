import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { handleRequest, release, releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const digest = value => createHash('sha256').update(value).digest('hex');

test('项目筛选可折叠，交付日期、搜索、各类数量及全量导出有明确入口', () => {
  const workbench = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(workbench, /展开筛选/);
  assert.match(workbench, /收起后.*生效.*清除/);
  assert.match(workbench, /选中项目/);
  assert.match(workbench, /交付记录页.*日期.*搜索.*合计.*各类型数量/);
  assert.match(workbench, /全部交付表.*当前筛选/);
});

test('当前归档分类说明区分恰好16、未知、旧配置与ZIP自动交付门槛', () => {
  const classification = html.match(/<details\b[^>]*id="archive-classification"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.match(classification, /最近一次有效交付.*单项目/);
  assert.match(classification, /恰好 16 个文件.*可运行.*其他已知数量.*生成材料/);
  assert.match(classification, /数量未知.*原分类/);
  assert.match(classification, /自动分类默认关闭/);
  assert.match(classification, /升级.*原有.*至少/);
  assert.match(classification, /预览.*确认/);
  assert.match(classification, /ZIP 自动交付.*每项目 4 个实际文件/);
  assert.match(html, /id="task-groups"[^>]*data-release-state="historical"[\s\S]*?4 个及以上/);
});

test('首页、安装说明和自动更新指向同一当前安装包与构建', async () => {
  const { filename, tag, sha256, size } = release;
  const downloadPath = `/download/${filename}`;
  const githubRelease = `https://github.com/232577/workbox/releases/tag/${tag}`;
  const githubAsset = `https://github.com/232577/workbox/releases/download/${tag}/${filename}`;
  const displaySize = size.toLocaleString('en-US');
  const stable = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  assert.equal(stable.status, 200);
  const metadata = await stable.json();
  assert.equal(metadata.tag_name, tag);
  assert.equal(metadata.html_url, githubRelease);
  assert.deepEqual(metadata.assets, [{ name: filename, size, state: 'uploaded', digest: `sha256:${sha256}`,
    browser_download_url: githubAsset }]);
  const source = metadata.body.match(/来源提交 ([a-f0-9]{40})。/)?.[1];
  assert.match(source, /^[a-f0-9]{40}$/);

  assert.ok(html.includes(`href="${downloadPath}"`) && html.includes(`href="${githubAsset}"`));
  assert.ok(html.includes(`${displaySize} 字节`) && html.includes(sha256) && html.includes(source));
  const rootReadme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
  const install = rootReadme.match(/\n## Windows 安装包\n([\s\S]*?)\n## /)?.[1];
  assert.ok(install?.includes(`[官网下载](https://workbox.ltxiangzi.dpdns.org${downloadPath})`));
  assert.ok(install.includes(`[GitHub Release](${githubRelease})`));
  for (const fact of [filename, displaySize, sha256]) assert.ok(install.includes(fact), fact);
  const siteReadme = await readFile(new URL('./README.md', import.meta.url), 'utf8');
  for (const fact of [filename, tag, displaySize, sha256, source]) assert.ok(siteReadme.includes(fact), fact);
  assert.ok(rootReadme.includes(source));
});

test('11月6日r1目录使用已核验候选的标签、字节数、安装包摘要和来源', async () => {
  assert.deepEqual(release, { version: '1.0.0', tag: 'v1.0.0.20261106.1', edition: '正式安装版',
    date: '2026-11-06', filename: 'WorkBox-1.0.0-Setup-x64-20261106-r1.exe', size: 40613745,
    sha256: 'bbdf096bb8929f5129b05df81387cb7a947fb5af15ec96badcbad7edd5f06969' });
  assert.equal(releases.length, 22);
  const metadata = await (await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {})).json();
  assert.match(metadata.body, /来源提交 9901f92db9d8794a4cb29d78947400c78a773e46。/);
  for (const fact of ['原项目状态', '原排序', '多个批次', '全部交付表', '恰好 16 个文件',
    '数量未知', '自动分类默认关闭', '旧规则仍按“至少”', '每项目 4 个实际文件']) {
    assert.ok(metadata.body.includes(fact), fact);
  }
  assert.doesNotMatch(metadata.body, /新建任务组|记录已交接|导出本组表格|手动交接/);
});

test('新增两张11月6日r1实拍保留最终EXE原始PNG及构建来源', async () => {
  const executable = '5fdbba35a02d00d716ce00ae4a8289802a7d8163e8fea30955469831249c2c1c';
  const source = '9901f92db9d8794a4cb29d78947400c78a773e46';
  assert.equal(registry.latest_screenshot_exe_sha256, executable);
  assert.equal(Object.keys(registry.files).length, 52);
  for (const [name, width, height, size, hash] of [
    ['workbox-ui-cleanup-light-20261106-r1.png', 1102, 792, 67409, '4420d21a048b869d932296017e5b8f0f6c0d9742d68b1c55a55bce88607b3afc'],
    ['workbox-ui-cleanup-narrow-dark-20261106-r1.png', 662, 512, 52355, '7001673ebc9489563bde345fce0f15601558593c4d537cb66e3c4fe377b4ef99'],
  ]) {
    const key = `public/media/${name}`;
    const bytes = await readFile(new URL(`./${key}`, import.meta.url));
    assert.equal(bytes.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(bytes.readUInt32BE(16), width);
    assert.equal(bytes.readUInt32BE(20), height);
    assert.equal(bytes.length, size);
    assert.equal(digest(bytes), hash);
    assert.equal(registry.files[key].size, size);
    assert.equal(registry.files[key].sha256, hash);
    assert.equal(registry.files[key].source_commit, source);
    assert.equal(registry.files[key].source_exe_sha256, executable);
    assert.ok(html.includes(`href="/media/${name}"`));
    assert.ok(html.includes(`src="/media/${name}" width="${width}" height="${height}"`));
  }
});

test('保留截至11月5日r3全部21项目录和50项媒体事实及原字节', async () => {
  const previous = releases.slice(releases.findIndex(item => item.tag === 'v1.0.0.20261105.3'));
  assert.equal(previous.length, 21);
  assert.equal(digest(JSON.stringify(previous)), '72811e159eec1319c96eee71d617b849fbfcc6afc96524bdbcb4ef54ebf765d3');
  for (const { filename } of previous) assert.ok(html.includes(`/download/${filename}`), filename);

  const previousMedia = Object.fromEntries(Object.entries(registry.files)
    .filter(([path]) => !path.includes('-20261106-r1.')));
  assert.equal(Object.keys(previousMedia).length, 50);
  assert.equal(digest(JSON.stringify(previousMedia)), '96be36292e343852bc1c242c02eb23b698e48957c79695e8dab8743e90f8f60a');
  for (const [path, facts] of Object.entries(previousMedia)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, facts.size, path);
    assert.equal(digest(bytes), facts.sha256, path);
  }
});
