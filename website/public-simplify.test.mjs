import test from 'node:test';
import assert from 'node:assert/strict';
import { documents, parseTags } from './public-documents.test-helper.mjs';
import { release } from './worker.mjs';

test('首页只有一个安装包主入口，下载锚点和安装边界一起位于首屏', () => {
  const home = documents['/index.html'];
  const downloads = parseTags(home).filter(tag => tag.name === 'a' &&
    tag.attrs.href === `/download/${release.filename}`);
  assert.equal(downloads.length, 1, '同一安装包不再重复占据两个宣传区');
  const hero = home.split('<!-- /home-hero -->')[0];
  for (const id of ['download', 'download-title', 'checksum', 'copy-checksum', 'copy-status']) {
    assert.ok(parseTags(hero).some(tag => tag.attrs.id === id), `${id} 留在首屏下载区`);
  }
  assert.match(hero, /Windows 10 1809\+ \/ Windows 11 x64/);
  assert.match(hero, /未做商业代码签名/);
  assert.match(hero, /升级前请备份数据和材料/);
  assert.match(hero, /href="\/guide\.html#installation"/);
  assert.match(hero, /GitHub 备用下载/);
});

test('首页页脚不重复主导航，许可、主题与私密统计入口继续可用', () => {
  const footer = documents['/index.html'].split('<footer')[1];
  const links = parseTags(footer).filter(tag => tag.name === 'a').map(tag => tag.attrs.href);
  assert.ok(!links.includes('/guide.html'));
  assert.ok(!links.includes('/releases.html'));
  for (const href of ['https://github.com/232577/workbox',
    'https://github.com/232577/workbox/blob/main/LICENSE',
    'https://github.com/232577/workbox/blob/main/THIRD_PARTY_NOTICES.md', '/stats/']) {
    assert.ok(links.includes(href), href);
  }
  assert.match(footer, /data-theme-select/);
  assert.match(footer, /统计仅所有者可见/);
});

test('版本页当前来源身份与校验值一起按需展开，仍保留精确提交链接', () => {
  const current = documents['/releases.html'].match(/<section id="current-release"[^>]*>([\s\S]*?)<\/section>/)?.[1];
  const details = current?.match(/<details class="package-details">([\s\S]*?)<\/details>/)?.[1];
  assert.ok(details);
  assert.match(details, /href="https:\/\/github\.com\/232577\/workbox\/commit\/[a-f0-9]{40}"/);
  assert.ok(details.includes(release.sha256));
  assert.doesNotMatch(current.replace(/<details\b[\s\S]*?<\/details>/g, ''), /[a-f0-9]{40}/);
});
