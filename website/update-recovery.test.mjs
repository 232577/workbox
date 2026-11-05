import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {handleRequest} from './worker.mjs';

test('稳定清单发布11月5日任务组包的实际摘要与大小', async () => {
  const response=await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'),{});
  const metadata=await response.json();
  assert.equal(metadata.tag_name,'v1.0.0.20261105.1');
  assert.equal(metadata.assets[0].name,'WorkBox-1.0.0-Setup-x64-20261105-r1.exe');
  assert.equal(metadata.assets[0].size,40622040);
  assert.equal(metadata.assets[0].digest,'sha256:a468a966befb8863e132d3510991fc715f8aa6938d7e1115388a8c70ef1e4537');
});

test('下载页明确说明只退出未升级的恢复办法和原目录数据保留', async () => {
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/id="update-recovery"/);
  assert.match(html,/只退出/);
  assert.match(html,/原安装目录/);
  assert.match(html,/不要卸载/);
  assert.match(html,/不要删除 data/);
  assert.match(html,/40,622,040/);
});

test('页面明确只修复悬浮球置顶，展开管理窗口不置顶', async () => {
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/小悬浮球/);
  assert.match(html,/展开的管理窗口不置顶/);
  assert.match(html,/不抢输入焦点/);
});

test('匿名实机截图标记11月4日r2构建', async () => {
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/截图来自 v1\.0\.0\.20261104\.2 匿名实机截图/);
  assert.doesNotMatch(html,/来自当前安装包的匿名隔离示例/);
});
