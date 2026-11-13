import test from 'node:test';
import assert from 'node:assert/strict';
import { documents, parseTags } from './public-documents.test-helper.mjs';
import { release, releases } from './worker.mjs';

const home = documents['/index.html'];
const guide = documents['/guide.html'];
const history = documents['/releases.html'];

test('三个独立页面有各自的中文文档、唯一主标题与唯一ID', () => {
  for (const [path, html] of Object.entries(documents)) {
    assert.ok(html.trim(), `${path} 必须实际存在`);
    const tags = parseTags(html);
    assert.equal(tags.find(tag => tag.name === 'html')?.attrs.lang, 'zh-CN', path);
    assert.equal(tags.filter(tag => tag.name === 'h1').length, 1, path);
    const ids = tags.filter(tag => Object.hasOwn(tag.attrs, 'id')).map(tag => tag.attrs.id);
    assert.equal(new Set(ids).size, ids.length, `${path} ID不得重复`);
  }
});

test('三页内链指向真实页面和唯一锚点，不依赖旧首页重定向兜底', () => {
  const origin = 'https://workbox.ltxiangzi.dpdns.org';
  for (const [path, html] of Object.entries(documents)) {
    for (const tag of parseTags(html).filter(tag => tag.name === 'a' && tag.attrs.href)) {
      const target = new URL(tag.attrs.href, `${origin}${path}`);
      if (target.origin !== origin) continue;
      const targetPath = target.pathname === '/' ? '/index.html' : target.pathname;
      if (!Object.hasOwn(documents, targetPath)) continue;
      assert.ok(documents[targetPath].trim(), `${path}: ${tag.attrs.href}`);
      if (!target.hash) continue;
      const id = decodeURIComponent(target.hash.slice(1));
      assert.equal(parseTags(documents[targetPath]).filter(item => item.attrs.id === id).length, 1,
        `${path}: ${tag.attrs.href} 必须在目标文档存在`);
    }
  }
});

test('首页只保留产品、真实界面和下载，完整演示教程与历史移出首页', () => {
  for (const id of ['actual-ui', 'overview', 'download', 'checksum', 'copy-checksum', 'copy-status']) {
    assert.ok(parseTags(home).some(tag => tag.attrs.id === id), id);
  }
  for (const id of ['guide', 'try-workbox', 'workbox-demo', 'floating-inbox', 'workflow', 'features',
    'daily-workbench', 'project-batches', 'settings', 'faq', 'quickstart-video', 'narrow-project-preview',
    'release-history', 'legacy-task-groups', 'task-groups']) {
    assert.ok(!parseTags(home).some(tag => tag.attrs.id === id), `${id} 不得继续嵌入首页`);
  }
  assert.doesNotMatch(home, /<video\b|data-demo-root|data-workflow-panel|class="wb-release-notes"/);
  assert.equal(parseTags(home).filter(tag => tag.attrs.href?.startsWith('/download/') &&
    tag.attrs.href !== `/download/${release.filename}`).length, 0, '首页不能继续列历史安装包');
  assert.ok(home.length < 22000, '首页不能把手册换个容器继续塞回去');
});

test('指南独立承载原演示、操作与来源，历史页面承载全部真实发布', () => {
  const ids = parseTags(guide).map(tag => tag.attrs.id);
  for (const id of ['guide', 'try-workbox', 'workbox-demo', 'quickstart-video', 'floating-inbox', 'workflow',
    'features', 'daily-workbench', 'project-batches', 'settings', 'faq', 'narrow-project-preview',
    'online-update', 'update-recovery']) assert.ok(ids.includes(id), id);
  for (const id of ['release-history', 'legacy-task-groups', 'task-groups']) {
    assert.ok(parseTags(history).some(tag => tag.attrs.id === id), id);
  }
  const links = parseTags(history).filter(tag => tag.name === 'a').map(tag => tag.attrs.href);
  assert.equal(releases.length, 34, '当前正式版及33个历史版');
  for (const item of releases) assert.ok(links.includes(`/download/${item.filename}`), item.filename);
  assert.ok(history.includes(release.sourceCommit ?? '6278c1c3319468e4f0658e0cf1b086d756bd44d2'));
});

test('安装指南保留首次安装和平台支持的事实边界', () => {
  const installation = guide.match(/<section\b[^>]*id="installation"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  assert.ok(installation);
  assert.match(installation, /安装包不包含已有项目/);
  assert.match(installation, /首次使用自动建立数据目录/);
  assert.match(installation, /暂不支持 macOS、ARM 和 32 位系统/);
  assert.match(installation, /实际验收范围以对应发布记录为准/);
});
