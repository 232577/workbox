import { archivedReadmes } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';
import { handleRequest, release, releases } from './worker.mjs';

const source = '6278c1c3319468e4f0658e0cf1b086d756bd44d2';
const expected = { version: '1.0.0', tag: 'v1.0.0.20261113.1', edition: '正式安装版', date: '2026-11-13',
  filename: 'WorkBox-1.0.0-Setup-x64-20261113-r1.exe', size: 42422935,
  sha256: 'fb6c2b5f29df4af3b6858d79676d9e2cce2310b6f00e16971a78d190fa34c19d' };

test('11月13日r1当前下载与更新清单绑定同一已核验构建，33个旧安装包逐字保留', async () => {
  assert.deepEqual(release, expected);
  assert.equal(releases.length, 34);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'f7d6550b01a7bca7f04fa5c6b1704e0abcc06138df93d9c3debce6aafbf5e76a');
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, expected.tag);
  assert.ok(metadata.body.includes(`来源提交 ${source}。`));
  assert.deepEqual(metadata.assets, [{ name: expected.filename, size: expected.size, state: 'uploaded',
    digest: `sha256:${expected.sha256}`,
    browser_download_url: `https://github.com/232577/workbox/releases/download/${expected.tag}/${expected.filename}` }]);
});

test('指南、版本页、两份说明与稳定更新准确解释导出范围和合并原批次', async () => {
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  const readme = archivedReadmes.product;
  const siteReadme = archivedReadmes.website;
  for (const text of [documents['/guide.html'], documents['/releases.html'], readme, siteReadme, metadata.body]) {
    for (const phrase of ['导出项目表', '当前筛选结果', '已勾选项目', '首次新增日期', '原建项批次', '原批次', '连续合并']) {
      assert.ok(text.includes(phrase), phrase);
    }
    assert.match(text, /(?:空选择|无勾选|未勾选).*不会.*(?:全部|全量)/);
    assert.match(text, /原单列待开发名单.*保留/);
    assert.match(text, /刷新.*(?:恢复|重新显示)/);
  }
  assert.doesNotMatch(readme, /尚不包含在上面的 r4 安装包中/);
  const notes = await readFile(new URL('../RELEASE_NOTES.md', import.meta.url), 'utf8');
  const current = notes.split('\n---\n')[0];
  for (const value of [source, expected.filename, expected.sha256]) assert.ok(current.includes(value));
  assert.doesNotMatch(current, /发布准备|待本构建|第二台物理设备.*通过/);
  const legacy = documents['/guide.html'].match(/<details\b[^>]*id="pending-project-export"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.match(legacy, /导出项目表.*菜单.*“全部待开发名单（仅名称）”.*“导出名单”/);
});

test('新说明用两段操作指引交代导出和合并，不向访客展开实现细节', async () => {
  const guide = documents['/guide.html'];
  const exportDetails = guide.match(/<details\b[^>]*id="project-table-export"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  const mergeDetails = guide.match(/<details><summary>合并后怎样保留原批次？<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.equal([...exportDetails.matchAll(/<p>/g)].length, 2);
  assert.equal([...mergeDetails.matchAll(/<p>/g)].length, 2);
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  for (const text of [exportDetails, documents['/releases.html'], metadata.body]) {
    assert.match(text, /状态、类型、首次新增日期和批次筛选.*导出项目表.*当前筛选结果/);
    assert.match(text, /批量选择.*勾选.*已勾选项目/);
    assert.match(text, /显示项目数/);
    assert.match(text, /无勾选.*不可用.*不会导出全部/);
    assert.match(text, /首次新增日期不随重复收件或状态变更改变/);
    assert.match(text, /名称、状态、类型、首次新增日期、收件批次编号、加急、截止日期、备注和范围说明/);
    assert.match(text, /全部待开发名单（仅名称）/);
  }
  for (const text of [mergeDetails, documents['/releases.html'], metadata.body]) {
    assert.match(text, /原建项批次不会被新收件批次遮住/);
    assert.match(text, /刷新.*恢复原批次.*无须重做合并/);
    assert.match(text, /连续合并/);
    assert.match(text, /同批按项目只计一次/);
    assert.match(text, /首次新增日期沿用保留项目/);
  }
  for (const text of [exportDetails, mergeDetails, documents['/releases.html'], metadata.body]) {
    assert.doesNotMatch(text, /同一只读快照|各原项目分别保留有效收件和任务来源|最终项目身份/);
  }
});

test('两种项目表范围适用所有状态，旧单列名单步骤必须先选择待开发', async () => {
  const metadata = await (await handleRequest(new Request('https://example.com/updates/stable.json'), {})).json();
  const readme = archivedReadmes.product;
  const siteReadme = archivedReadmes.website;
  const exportDetails = documents['/guide.html'].match(/<details\b[^>]*id="project-table-export"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.match(exportDetails, /两种范围适用于全部项目状态/);
  let instructions = 0;
  for (const text of [documents['/guide.html'], documents['/releases.html'], metadata.body, readme, siteReadme]) {
    const steps = text.split(/[。！？]/).filter(sentence => sentence.includes('全部待开发名单（仅名称）'));
    assert.ok(steps.length > 0, '每个当前说明入口都应有旧名单操作指引');
    for (const step of steps) {
      assert.match(step, /先选择“待开发”，再打开“导出项目表”菜单，选择“全部待开发名单（仅名称）”/);
      instructions++;
    }
  }
  assert.equal(instructions, 8, '指南两处、版本页两处、更新说明两处、两份README各一处');
});
