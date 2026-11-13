import test from 'node:test';
import assert from 'node:assert/strict';
import { documents, parseTags } from './public-documents.test-helper.mjs';
import { releases } from './worker.mjs';
import { readFile } from 'node:fs/promises';
import { archivedReadmes } from './public-documents.test-helper.mjs';

test('精简说明保留当前安装身份和明确存档入口，原发布事实分别留存', async () => {
  for (const [path, link, archived] of [
    ['../README.md', 'docs/releases/RELEASE-NOTES-ARCHIVE.md', archivedReadmes.product],
    ['./README.md', '../docs/releases/RELEASE-NOTES-ARCHIVE.md', archivedReadmes.website],
  ]) {
    const text = await readFile(new URL(path, import.meta.url), 'utf8');
    assert.ok(text.includes(`](${link})`), path);
    assert.ok(text.split('\n').length < 100, `${path} 是简明入口`);
    assert.doesNotMatch(text, /历史正式安装版 ·/);
    if (path === '../README.md') {
      for (const value of [releases[0].filename, releases[0].sha256, releases[0].tag,
        releases[0].size.toLocaleString('en-US'), 'ff4a336d66adb65c6cfedfb5b258067c703dee69']) assert.ok(text.includes(value), `${path}: ${value}`);
    } else {
      assert.match(text, /\]\(worker\.mjs\)/);
      assert.match(text, /\]\(https:\/\/workbox\.ltxiangzi\.dpdns\.org\/releases\.html\)/);
    }
    assert.match(archived, /首次新增日期/);
    assert.match(archived, /标记加急/);
    assert.match(archived, /原建项批次/);
  }
});

test('首页以一句用途、下载和同一首屏的真实工作台组织产品入口', () => {
  const home = documents['/index.html'];
  const hero = home.match(/<div class="home-hero site-width">([\s\S]*?)<!-- \/home-hero -->/)?.[1];
  assert.ok(hero, '产品标题与真实窗口属于一个响应式首屏');
  assert.match(hero, /id="hero-title"/);
  assert.match(hero, /id="actual-ui"/);
  assert.match(hero, /data-desktop-image="light"/);
  assert.match(hero, /免费下载 Windows 版/);
  assert.doesNotMatch(hero, /<video|data-demo-root/);
});

test('当前更新默认只有三项重点，完整旧说明和34个历史包原生折叠保留', () => {
  const page = documents['/releases.html'];
  const highlights = page.match(/<ul class="release-highlights">([\s\S]*?)<\/ul>/)?.[1];
  assert.equal([...highlights?.matchAll(/<li>/g) ?? []].length, 3);
  const details = parseTags(page).filter(tag => tag.name === 'details');
  for (const id of ['release-details', 'historical-downloads']) {
    const item = details.find(tag => tag.attrs.id === id);
    assert.ok(item, id);
    assert.ok(!Object.hasOwn(item.attrs, 'open'), `${id} 默认收起`);
  }
  const history = page.match(/<details id="historical-downloads"[^>]*>([\s\S]*?)<\/details>/)?.[1];
  assert.ok(history);
  for (const release of releases.slice(1)) assert.ok(history.includes(`/download/${release.filename}`));
  assert.match(history, /34 个历史安装包/);
});
