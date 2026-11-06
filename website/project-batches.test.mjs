import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const section = html.match(/<section\b[^>]*id="project-batches"[^>]*>([\s\S]*?)<\/section>/)?.[1];
const daily = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];

test('项目批次说明保留四个原状态与原排序，一项目多批仍一行', () => {
  assert.ok(section, '项目批次有可定位说明');
  for (const phrase of ['待开发', '开发中', '待交付', '已交付', '原排序', '一项目一行',
    '同批颜色与编号', '一个项目可以属于多个批次', '两个编号', '+N批', '完整批次',
    '编号搜索', '批次筛选', '原建项批次', '工作目录']) assert.ok(section.includes(phrase), phrase);
  assert.doesNotMatch(section, /新建任务组|记录已交接|导出本组表格|材料就绪/);
  assert.doesNotMatch(section, /<(?:button|input|video|img)\b/);
});

test('主要入口说明沿用项目流程与全部交付表，不引入任务交接步骤', () => {
  const nav = html.match(/<nav\b[^>]*class="wb-nav"[^>]*>([\s\S]*?)<\/nav>/)?.[1];
  assert.match(nav, /href="#project-batches"/);
  assert.doesNotMatch(nav, /任务组/);
  assert.ok(daily?.includes('全部交付表'));
  assert.doesNotMatch(daily, /新建任务组|添加消息|记录已交接|导出本组|历史消息补录|高级任务操作/);
  assert.match(section, /全部交付表/);
  assert.match(section, /按项目去重/);
  assert.match(section, /首次有效交付/);
});

test('旧任务说明折叠保留为明确历史，原锚点继续可达', () => {
  assert.match(html, /<details\b[^>]*id="legacy-task-groups"[^>]*>\s*<summary>[^<]*历史[^<]*<\/summary>\s*<section\b[^>]*id="task-groups"[^>]*data-release-state="historical"/);
  assert.match(html, /固定任务号/);
  assert.match(html, /不会代你发送文件/);
  assert.match(html, /任务数不计入首次交付项目数/);
});

test('新增r3后保持已核验的20项旧下载目录与48项旧媒体字节', async () => {
  const index = releases.findIndex(item => item.tag === 'v1.0.0.20261105.3');
  assert.ok(index >= 0);
  assert.equal(releases.length - index, 21);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(index + 1))).digest('hex'),
    'b14365f75280ddbae09349b0e3deff206f0886601fc0688cbcbdde4add4c1f29');
  const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
  assert.equal(Object.keys(registry.files).filter(path => !path.includes('-20261106-r1.')).length, 50);
  for (const [path, fact] of Object.entries(registry.files)) {
    const bytes = await readFile(new URL(`./${path}`, import.meta.url));
    assert.equal(bytes.length, fact.size, path);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), fact.sha256, path);
  }
});
