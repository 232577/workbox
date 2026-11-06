import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { releases } from './worker.mjs';

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
