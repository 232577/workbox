import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { releases } from './worker.mjs';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const section = html.match(/<section\b[^>]*id="task-groups"[^>]*>([\s\S]*?)<\/section>/)?.[1];

test('正式任务功能说明与下载区同步，不遗留下一版准备文案', () => {
  assert.ok(section, '应有独立任务组说明');
  assert.doesNotMatch(section, /下一版准备中|当前正式安装版仍以下载区标注为准/);
  assert.match(html, /id="task-groups"[^>]*data-release-state="published"/);
  assert.match(html, /href="#task-groups"/);
  assert.doesNotMatch(section, /<(?:input|button|video|img)\b/);
});

test('任务说明保留版本、人工交接和项目统计的区别，原生详情可逐项展开', () => {
  assert.ok(section);
  assert.equal([...section.matchAll(/<details\b/g)].length, 4);
  for (const phrase of ['固定任务号', '“添加消息”', '所选任务组', '收件日期和当日序号', '组内序号',
    '排序或改名不改变编号', '新一轮任务', '材料就绪', '记录已交接',
    '不会代你发送文件', '交付项目', '类型汇总', '任务组汇总', '任务明细', '导出本组表格',
    '任务数不计入首次交付项目数', '撤销或恢复', '重新关联', '最近一次有效交付', '未知数量保持原分类',
    '规则默认关闭', '不代表已经验证软件可运行']) assert.ok(section.includes(phrase), phrase);
});

test('新发布保留全部十八项旧下载及四十四项原媒体来源，不改变赞助配置', async () => {
  assert.equal(releases.length, 19);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),
    '1b5712f062ff03104c55336e4e194ae119845ed900f11c0be1b37e267ce64ce0');
  const media = await readFile(new URL('./MEDIA.json', import.meta.url));
  assert.equal(Object.keys(JSON.parse(media).files).length, 44);
  assert.equal(createHash('sha256').update(media).digest('hex'),
    '7a36f65d41aa6a5037f00088263b58044ce594c30f62b7d76935fbad89d5a951');
  assert.deepEqual(JSON.parse(await readFile(new URL('./public/support.json', import.meta.url), 'utf8')),
    { enabled: false, methods: [] });
});
