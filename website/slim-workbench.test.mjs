import { siteHtml, documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const html = siteHtml;
const section = html.match(/<section\b[^>]*id="daily-workbench"[^>]*>([\s\S]*?)<\/section>/)?.[1];

test('全部历史下载仅出现于独立版本页的历史列表，每份HTML完整结束', async () => {
  const { releases } = await import('./worker.mjs');
  const releasePage = documents['/releases.html'];
  const history = releasePage.match(/<section\b[^>]*id="release-history"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.ok(history, '历史版本章节存在');
  const links = history.match(/<div\b[^>]*class="wb-release-links"[^>]*>([\s\S]*?)<\/div>/)?.[1];
  assert.ok(links, '历史下载列表位于版本章节内');
  const hrefs = [...links.matchAll(/<a\b[^>]*href="([^"]+)"/g)].map(match => match[1]).filter(href => href.startsWith('/download/'));
  assert.deepEqual(hrefs, releases.slice(1).map(item => `/download/${item.filename}`));
  for (const href of hrefs) {
    assert.equal([...releasePage.matchAll(/<a\b[^>]*href="([^"]+)"/g)].filter(match => match[1] === href).length, 1, href);
  }
  for (const page of Object.values(documents)) assert.match(page, /<\/body>\s*<\/html>\s*$/);
});

test('精简工作台说明准确交代日常入口、条件提醒和高级工具去向', () => {
  assert.ok(section);
  for (const phrase of ['打开先看项目', '待开发、开发中、待交付、已交付', '今日工作量', '高级设置', '待核对数量大于零',
    '正在查看', '离开', '待核对合包或归档错误', '不受项目搜索或筛选影响',
    '工作设置', '归档分类', 'ZIP归档库', '收录记录', '监视详情',
    '外观', '使用指南', '检查更新', '所选项目的操作菜单', '选中项目后查看对应文档和项目操作']) {
    assert.ok(section.includes(phrase), phrase);
  }
  assert.doesNotMatch(section, /<(?:button|input|video|img)\b/);
});

test('日常说明保留项目状态、收件定位与全部交付导出，不增设任务步骤', () => {
  for (const phrase of ['粘贴消息', '本次项目', '沿用的同名项目', '原状态和目录',
    '项目操作确认交付', '全部交付表']) assert.ok(section?.includes(phrase), phrase);
  assert.doesNotMatch(section, /新建任务组|记录已交接|导出本组表格|高级任务操作/);
});
