import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { release, releases, handleRequest } from './worker.mjs';

test('r3目录使用核验过的安装器，完整保留原20项下载及字段', () => {
  assert.deepEqual(release, { version: '1.0.0', tag: 'v1.0.0.20261105.3', edition: '正式安装版',
    date: '2026-11-05', filename: 'WorkBox-1.0.0-Setup-x64-20261105-r3.exe', size: 40609504,
    sha256: '533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a' });
  assert.equal(releases.length, 21);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    'b14365f75280ddbae09349b0e3deff206f0886601fc0688cbcbdde4add4c1f29');
});

test('r3更新说明与首页及README一致，只宣传原项目流程和全部导出', async () => {
  const metadata = await (await handleRequest(new Request('https://example.invalid/updates/stable.json'), {})).json();
  assert.equal(metadata.tag_name, 'v1.0.0.20261105.3');
  assert.equal(metadata.assets[0].size, 40609504);
  assert.equal(metadata.assets[0].digest, 'sha256:533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a');
  for (const phrase of ['原项目状态', '原排序', '一项目一行', '多个批次', '全部交付表',
    '6eb83f50ce3eba52df0da9439be3243afc47df26']) assert.ok(metadata.body.includes(phrase), phrase);
  assert.doesNotMatch(metadata.body, /新建任务组|记录已交接|导出本组表格|手动交接/);
  for (const path of ['./public/index.html', './README.md', '../README.md']) {
    const text = await readFile(new URL(path, import.meta.url), 'utf8');
    for (const phrase of ['v1.0.0.20261105.3', 'WorkBox-1.0.0-Setup-x64-20261105-r3.exe',
      '533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a',
      '6eb83f50ce3eba52df0da9439be3243afc47df26', '全部交付表']) assert.ok(text.includes(phrase), `${path}: ${phrase}`);
  }
});
