import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const publicRoot = new URL('./public/', import.meta.url);
const brandUrl = '/favicon.svg?v=20261112-cube';

test('网站立方品牌与桌面矢量资产完全一致且不依赖字体', () => {
  const web = readFileSync(new URL('favicon.svg', publicRoot), 'utf8');
  const desktop = readFileSync(new URL('../assets/workbox.svg', import.meta.url), 'utf8');
  assert.equal(web, desktop);
  for (const id of ['cube-top', 'cube-left', 'cube-right', 'cube-w']) {
    assert.ok(web.includes(`id="${id}"`));
  }
  assert.doesNotMatch(web, /<(?:text|image)\b/);
});

for (const page of readdirSync(publicRoot).filter((name) => name.endsWith('.html'))) {
  test(`${page} 的页签、页头和演示图标统一使用新品牌缓存版本`, () => {
    const html = readFileSync(new URL(page, publicRoot), 'utf8');
    const urls = [...html.matchAll(/(?:src|href)="(\/favicon\.svg[^"\s]*)"/g)]
      .map((match) => match[1]);
    assert.ok(urls.length >= 2, '页签图标和页面品牌都应保留');
    for (const url of urls) assert.equal(url, brandUrl);
  });
}
