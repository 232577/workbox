import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { documents } from './public-documents.test-helper.mjs';

const css = await readFile(new URL('./public/release-page.css', import.meta.url), 'utf8');
const homepageCss = await readFile(new URL('./public/homepage.css', import.meta.url), 'utf8');
const baseCss = await readFile(new URL('./public/styles.css', import.meta.url), 'utf8');
const themeCss = await readFile(new URL('./public/theme.css', import.meta.url), 'utf8');

function contrast(first, second) {
  const luminance = color => {
    const channels = color.replace('#', '').match(/../g).map(value => parseInt(value, 16) / 255);
    return channels.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  };
  const [low, high] = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (high + .05) / (low + .05);
}

function rules(source, media = []) {
  source = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const result = [];
  let cursor = 0;
  while (cursor < source.length) {
    const start = source.indexOf('{', cursor);
    if (start < 0) break;
    const selector = source.slice(cursor, start).trim();
    let end = start + 1, depth = 1;
    for (; end < source.length && depth; end++) {
      if (source[end] === '{') depth++;
      else if (source[end] === '}') depth--;
    }
    assert.equal(depth, 0, `Unclosed CSS rule: ${selector}`);
    const body = source.slice(start + 1, end - 1);
    if (selector.startsWith('@media')) result.push(...rules(body, [...media, selector]));
    else result.push({ selector, media, declarations:Object.fromEntries(body.split(';')
      .filter(value => value.includes(':'))
      .map(value => value.split(/:(.*)/s).slice(0, 2).map(part => part.trim()))) });
    cursor = end;
  }
  return result;
}

const stylesheet = rules(css);
const normalRules = stylesheet.filter(rule => rule.media.length === 0);
const base = normalRules.find(rule => rule.selector === '.home-page').declarations;
const customProperties = values => Object.fromEntries(Object.entries(values).filter(([key]) => key.startsWith('--')));
const dark = normalRules.find(rule => rule.selector === 'html[data-theme="dark"] .home-page').declarations;
const appearances = { light:customProperties(base), dark:{ ...customProperties(base), ...customProperties(dark) } };

function resolve(value, tokens, seen = []) {
  return value.replace(/var\((--[\w-]+)\)/g, (_, key) => {
    assert.ok(Object.hasOwn(tokens, key), `Missing color token: ${key}`);
    assert.ok(!seen.includes(key), `Circular color token: ${key}`);
    return resolve(tokens[key], tokens, [...seen, key]);
  });
}

// Only the concrete selectors on these elements are needed; respect their CSS
// specificity and source order, including the generic link hover rule.
function style(selectors, sourceRules = normalRules) {
  const specificity = selector => {
    const ids = (selector.match(/#[\w-]+/g) ?? []).length;
    const classes = (selector.match(/\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+/g) ?? []).length;
    const types = (selector.replace(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|::?[\w-]+/g, '').match(/\b[a-z][\w-]*\b/g) ?? []).length;
    return ids * 10000 + classes * 100 + types;
  };
  const matches = sourceRules.flatMap((rule, order) => rule.selector.split(',').map(value => value.trim())
    .filter(value => selectors.includes(value))
    .map(selector => ({ ...rule, order, weight:specificity(selector) })));
  matches.sort((a, b) => a.weight - b.weight || a.order - b.order);
  return Object.assign({}, ...matches.map(rule => rule.declarations));
}

function colors(tokens, ...ancestors) {
  let result = {};
  for (const declarations of [base, ...ancestors]) {
    const color = declarations.color;
    const background = declarations['background-color'] ?? declarations.background;
    if (color && color !== 'inherit') result.color = resolve(color, tokens);
    if (background && !['transparent', 'none', 'inherit'].includes(background)) result.background = resolve(background, tokens);
  }
  for (const value of Object.values(result)) assert.match(value, /^#[\da-f]{6}$/i, 'Contrast must use resolved, opaque colors');
  assert.ok(result.color && result.background, 'Foreground and actual backing surface must both be known');
  return result;
}

function assertReadable(label, palette) {
  const ratio = contrast(palette.color, palette.background);
  assert.ok(ratio >= 4.5, `${label}: ${palette.color} on ${palette.background} has ${ratio.toFixed(2)}:1 contrast`);
}

test('浅深主题的示例ZIP标签及免责声明均按小字达到4.5对比度', () => {
  const demo = style(['.home-page .wb-demo']);
  const file = style(['.home-page .wb-demo-file', '.home-page .wb-demo-file.zip']);
  const label = style(['.home-page .wb-demo-file b', '.home-page .wb-demo-file.zip b']);
  const disclaimer = style(['.home-page .wb-demo-disclaimer']);
  assert.ok(file.color && label.background && disclaimer.color && disclaimer.background);
  for (const [appearance, tokens] of Object.entries(appearances)) {
    assertReadable(`${appearance} ZIP`, colors(tokens, demo, file, label));
    assertReadable(`${appearance} disclaimer`, colors(tokens, demo, disclaimer));
  }
});

test('跳转链接在浅深主题的正常和悬停状态可读，焦点层高于粘性导航', () => {
  const normal = style(['.home-page a', '.home-page .skip']);
  const hover = style(['.home-page a', '.home-page .skip', '.home-page a:hover', '.home-page .skip:hover']);
  assert.ok(Object.values(documents).every(html => /<header\b[^>]*class="site-header"/.test(html)));
  const header = style(['.site-header', '.home-page .site-header'], [...rules(homepageCss).filter(rule => !rule.media.length), ...normalRules]);
  if (['sticky', 'fixed'].includes(header.position)) {
    assert.ok(Number.isFinite(Number(header['z-index'])), '固定的实际导航层级必须明确');
    assert.ok(Number(normal['z-index']) > Number(header['z-index']), '跳转入口应显示在实际导航上方');
  }
  assert.ok(normal.background, 'The skip link needs an opaque backing surface');
  for (const [appearance, tokens] of Object.entries(appearances)) {
    assertReadable(`${appearance} skip`, colors(tokens, normal));
    assertReadable(`${appearance} skip:hover`, colors(tokens, hover));
  }
});

test('无脚本系统深色与显式深色的全部主题token和覆盖等价，并只匹配无明确偏好', () => {
  const explicit = normalRules.filter(rule => rule.selector.startsWith('html[data-theme="dark"]'));
  const fallback = stylesheet.filter(rule => rule.media.length === 1 && /^@media\s*\(prefers-color-scheme:\s*dark\)$/.test(rule.media[0]));
  assert.ok(explicit.length > 0 && fallback.length > 0, 'Both explicit and script-free system dark appearances are required');
  assert.ok(fallback.every(rule => rule.selector.split(',').every(selector => selector.trim().startsWith('html:not([data-theme]) '))), 'System dark must not override an explicit light preference');
  assert.deepEqual(fallback.map(rule => ({ selector:rule.selector, declarations:rule.declarations })),
    explicit.map(rule => ({ selector:rule.selector.replaceAll('html[data-theme="dark"]', 'html:not([data-theme])'), declarations:rule.declarations })));
  const releaseTokens = Object.keys(base).filter(key => key.startsWith('--release-')).sort();
  assert.ok(releaseTokens.length > 0, 'The actual homepage surface and text tokens must be covered');
  assert.deepEqual(Object.keys(dark).filter(key => key.startsWith('--release-')).sort(), releaseTokens);
  const fallbackTokens = { ...customProperties(base), ...customProperties(fallback.find(rule => rule.selector === 'html:not([data-theme]) .home-page').declarations) };
  assert.deepEqual(fallbackTokens, appearances.dark);
  for (const key of Object.keys(fallbackTokens)) {
    assert.equal(resolve(fallbackTokens[key], fallbackTokens), resolve(appearances.dark[key], appearances.dark), `Resolved ${key} differs`);
  }
});

test('窄屏指南和版本目录的首个章节入口仍可见', () => {
  for (const width of [320, 700, 760, 1024]) {
    const activeRules = [baseCss, homepageCss, themeCss, css].flatMap(source => rules(source))
      .filter(rule => rule.media.every(query => {
        const maximum = query.match(/^@media\s*\(max-width:\s*(\d+)px\)$/);
        return maximum ? width <= Number(maximum[1]) : false;
      }));
    const firstChapter = style(['a', 'nav a', 'nav a:first-child', '.home-page a', '.docs-index a',
      '.docs-page .docs-index a', '.home-page .docs-index a', 'nav.docs-index a'], activeRules);
    assert.notEqual(firstChapter.display, 'none', `${width}px: 安装与升级/当前版本不能被旧导航规则隐藏`);
  }
});
