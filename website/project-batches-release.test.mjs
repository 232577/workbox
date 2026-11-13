import { archivedReadmes } from './public-documents.test-helper.mjs';
import { siteHtml, documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { releases } from './worker.mjs';

test('r3目录使用核验过的安装器，完整保留原20项下载及字段', () => {
  const index = releases.findIndex(item => item.tag === 'v1.0.0.20261105.3');
  assert.ok(index >= 0);
  assert.deepEqual(releases[index], { version: '1.0.0', tag: 'v1.0.0.20261105.3', edition: '正式安装版',
    date: '2026-11-05', filename: 'WorkBox-1.0.0-Setup-x64-20261105-r3.exe', size: 40609504,
    sha256: '533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a' });
  assert.equal(releases.length - index, 21);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(index + 1))).digest('hex'),
    'b14365f75280ddbae09349b0e3deff206f0886601fc0688cbcbdde4add4c1f29');
});

test('r3保留带原标签和摘要的历史说明及下载链接', async () => {
  const html = siteHtml;
  const history = documents['/releases.html'].match(/<section\b[^>]*id="release-history"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.match(history, /href="\/download\/WorkBox-1\.0\.0-Setup-x64-20261105-r3\.exe"/);
  const readme = archivedReadmes.product;
  const notes = readme.match(/<details><summary>11月5日 r3历史发布说明<\/summary>([\s\S]*?)<\/details>/)?.[1];
  assert.ok(notes);
  for (const fact of ['v1.0.0.20261105.3', 'WorkBox-1.0.0-Setup-x64-20261105-r3.exe', '40,609,504',
    '533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a',
    '6eb83f50ce3eba52df0da9439be3243afc47df26', '全部交付表']) assert.ok(notes.includes(fact), fact);
  assert.doesNotMatch(notes, /新建任务组|记录已交接|导出本组表格|手动交接/);
});
