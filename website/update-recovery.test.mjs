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

test('升级说明区分可在线检查的个人版与须手动原地升级的团队版', async () => {
  const guides = documents['/guide.html'].match(/<p id="online-update">([\s\S]*?)<\/p>/)?.[1];
  const highlights = documents['/releases.html'].match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  const response = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/updates/stable.json'), {});
  const update = (await response.json()).body.split('\n\n')[0];
  for (const text of [guides, highlights, update]) {
    assert.match(text, /11 月 25 日 r1[^。<]*旧个人版[^。<]*高级设置 → 检查更新/);
    assert.match(text, /11 月 26 日 r1[^。<]*工作站可从[^。<]*高级设置 → 检查更新[^。<]*后续版本/);
    assert.match(text, /团队预览版[^。<]*11 月 25 日 r2[^。<]*内置检查受限/);
    assert.match(text, /退出工作箱[^。<]*手动运行[^。<]*工作站完整安装包[^。<]*原安装目录/);
    assert.match(text, /只更新桌面程序[^。<]*办公室服务器[^。<]*管理员单独维护/);
  }
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
  assert.equal(source, '327ba5ade2a8a71ac8b045bfea15569772842a49');
  assert.ok(actualUi.includes('来源提交 2432b7e237db6424bd70e01f9a96f7567c5e644e'));
  assert.ok(!actualUi.includes(`来自 ${release.tag} 的同一隔离实例`));
  assert.doesNotMatch(html,/来自当前安装包的匿名隔离示例/);
});
