import { siteHtml, documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {handleRequest,release} from './worker.mjs';

test('稳定清单返回当前安装包的实际摘要与大小', async () => {
  const response=await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'),{});
  const metadata=await response.json();
  assert.equal(metadata.tag_name,release.tag);
  assert.equal(metadata.assets[0].name,release.filename);
  assert.equal(metadata.assets[0].size,release.size);
  assert.equal(metadata.assets[0].digest,`sha256:${release.sha256}`);
});

test('下载页明确说明只退出未升级的恢复办法和原目录数据保留', async () => {
  const html = siteHtml;
  assert.match(html,/id="update-recovery"/);
  assert.match(html,/只退出/);
  assert.match(html,/原安装目录/);
  assert.match(html,/不要卸载/);
  assert.match(html,/不要删除 data/);
  assert.ok(html.includes(release.size.toLocaleString('en-US')));
});

test('页面明确只修复悬浮球置顶，展开管理窗口不置顶', async () => {
  const html = siteHtml;
  assert.match(html,/小悬浮球/);
  assert.match(html,/展开的管理窗口不置顶/);
  assert.match(html,/不抢输入焦点/);
});

test('中性实机项目截图保留准确r4来源，当前更新清单单独绑定新构建', async () => {
  const html = siteHtml;
  const actualUi=documents['/guide.html'];
  assert.ok(actualUi?.includes('来自 v1.0.0.20261109.4 的同一隔离实例'));
  const stable = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  const source = (await stable.json()).body.match(/来源提交 ([a-f0-9]{40})。/)?.[1];
  assert.match(source, /^[a-f0-9]{40}$/);
  assert.equal(source, '8b09af66f20932eb305fd0272ea648cf8dfc42a1');
  assert.ok(actualUi.includes('来源提交 2432b7e237db6424bd70e01f9a96f7567c5e644e'));
  assert.ok(!actualUi.includes(`来自 ${release.tag} 的同一隔离实例`));
  assert.doesNotMatch(html,/来自当前安装包的匿名隔离示例/);
});
