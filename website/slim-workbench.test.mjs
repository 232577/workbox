import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html = await readFile(new URL('./public/index.html', import.meta.url), 'utf8');
const section = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];

test('全部十九项历史下载仅出现于历史折叠列表，HTML结束后没有孤立内容', async () => {
  const { releases } = await import('./worker.mjs');
  const history = html.match(/<details\b[^>]*id="release-history"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.ok(history, '历史折叠容器存在');
  const links = history.match(/<div\b[^>]*class="wb-release-links"[^>]*>([\s\S]*?)<\/div>/)?.[1];
  assert.ok(links, '历史下载列表位于折叠容器内');
  const hrefs = [...links.matchAll(/<a\b[^>]*href="([^"]+)"/g)].map(match => match[1]).filter(href => href.startsWith('/download/'));
  assert.deepEqual(hrefs, releases.slice(1).map(item => `/download/${item.filename}`));
  for (const href of hrefs) {
    assert.equal([...html.matchAll(/<a\b[^>]*href="([^"]+)"/g)].filter(match => match[1] === href).length, 1, href);
  }
  assert.match(html, /<\/body>\s*<\/html>\s*$/);
});

test('精简工作台说明准确交代日常入口、条件提醒和高级工具去向', () => {
  assert.ok(section);
  for (const phrase of ['任务组、项目、今日工作量', '高级设置', '待核对数量大于零',
    '正在查看', '离开', '待核对合包或归档错误', '不受项目搜索或筛选影响',
    '工作设置', '归档分类', 'ZIP归档库', '收录记录', '监视详情', '历史消息补录',
    '外观', '使用指南', '检查更新', '所选项目的操作菜单', '业务规则保持']) {
    assert.ok(section.includes(phrase), phrase);
  }
  assert.doesNotMatch(section, /<(?:button|input|video|img)\b/);
});

test('任务操作说明保留四个主要动作、当前组上下文和高级任务操作', () => {
  for (const phrase of ['新建任务组', '添加消息', '记录已交接', '导出本组表格',
    '复制本组未交', '查看项目文件', '新一轮', '撤销交接', '关联交付记录',
    '核对本组消息', '当前任务组', '高级任务操作']) assert.ok(section?.includes(phrase), phrase);
});
