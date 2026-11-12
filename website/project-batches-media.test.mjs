import { siteHtml, documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const registry = JSON.parse(await readFile(new URL('./MEDIA.json', import.meta.url), 'utf8'));
const html = siteHtml;
const images = [
  ['workbox-project-batches-light-20261105-r3.png', 1186, 843, 'a003caf665bfcf54fcd026f38572e0a42440aaf74d29ded9b201d0fdf616e30a'],
  ['workbox-project-batches-narrow-dark-20261105-r3.png', 662, 512, 'd99997d9741a85eff5e8bf41b4807e80b45fdff4227ef02a5f9ea51182532aaa'],
];

test('两张r3主项目实拍绑定同一EXE与来源，旧48媒体不变', async () => {
  for (const [name, width, height, digest] of images) {
    const key = 'public/media/' + name, fact = registry.files[key];
    const bytes = await readFile(new URL('./' + key, import.meta.url));
    assert.equal(bytes.readUInt32BE(16), width);
    assert.equal(bytes.readUInt32BE(20), height);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), digest);
    assert.equal(fact.sha256, digest);
    assert.equal(fact.size, bytes.length);
    assert.equal(fact.source_commit, '6eb83f50ce3eba52df0da9439be3243afc47df26');
    assert.equal(fact.source_exe_sha256, '3db86834172f225584338465ee245398c16c56a544902d10c200f4108a75bc1d');
  }
  const media = Object.entries(registry.files);
  const old = Object.fromEntries(media.slice(media.findIndex(([path]) =>
    path === 'public/media/workbox-task-groups-light-20261105-r2.png')));
  assert.equal(Object.keys(old).length, 48);
  assert.equal(createHash('sha256').update(JSON.stringify(old)).digest('hex'), '8c3a0ef722b3b18bfb2aeced1d80b26b2aa35dc84806d81f88b4701e3dafc123');
});

test('宽浅主图与窄深对照分别呈现，窄图不冒充宽主题替换', () => {
  assert.match(html, /data-preview-mode="single"/);
  assert.match(html, /id="narrow-project-preview"/);
  assert.match(html, /窄窗深色对照/);
  const stage = documents['/index.html'].match(/<figure\b[^>]*data-desktop-stage[^>]*>([\s\S]*?)<\/figure>/)?.[1];
  assert.match(stage, /data-desktop-image="light"/);
  const narrow = html.match(/<figure\b[^>]*id="narrow-project-preview"[^>]*>[\s\S]*?<img\b[^>]*src="([^"]+)"/)?.[1];
  assert.ok(narrow);
  assert.ok(!stage.includes(narrow));
  assert.doesNotMatch(stage, /workbox-task-groups-/);
});
