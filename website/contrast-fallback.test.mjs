import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const css = await readFile(new URL('./public/release-page.css', import.meta.url), 'utf8');

function contrast(first, second) {
  const luminance = color => {
    const channels = color.replace('#', '').match(/../g).map(value => parseInt(value, 16) / 255);
    return channels.map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  };
  const [low, high] = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (high + .05) / (low + .05);
}

function declarations(selector) {
  const match = [...css.matchAll(/([^{}]+)\{([^{}]+)\}/g)]
    .find(value => value[1].trim() === selector);
  assert.ok(match, `Missing style: ${selector}`);
  return Object.fromEntries(match[2].split(';').filter(value => value.includes(':'))
    .map(value => value.split(/:(.*)/s).slice(0, 2).map(part => part.trim())));
}

test('示例ZIP标签及免责声明的小字对比度达到4.5而非按大字放宽', () => {
  const zip = declarations('.wb-demo-file.zip b');
  const disclaimer = declarations('.wb-demo-disclaimer');
  assert.ok(contrast('#ffffff', zip.background) >= 4.5);
  assert.ok(contrast(disclaimer.color, disclaimer.background) >= 4.5);
});

test('无脚本系统深色完整复用显式深色规则，不在浅色或明确选择浅色时启用', () => {
  const marker = '@media(prefers-color-scheme:dark)';
  const start = css.indexOf(marker);
  assert.ok(start >= 0, 'Missing system dark fallback');
  let end = css.indexOf('{', start), depth = 1;
  const contentStart = ++end;
  for (; end < css.length && depth; end++) {
    if (css[end] === '{') depth++;
    else if (css[end] === '}') depth--;
  }
  assert.equal(depth, 0);
  const fallback = css.slice(contentStart, end - 1);
  const explicit = [...css.slice(0, start).matchAll(/(html\[data-theme="dark"\][^{}]*)\{([^{}]+)\}/g)];
  assert.ok(explicit.length >= 10, 'Missing dark surface coverage');
  const fallbackRules = [...fallback.matchAll(/([^{}]+)\{([^{}]+)\}/g)];
  assert.equal(fallbackRules.length, explicit.length, 'Fallback must cover every existing dark rule');
  for (const [index, rule] of explicit.entries()) {
    assert.equal(fallbackRules[index][1].trim(), rule[1].replaceAll('html[data-theme="dark"]', 'html:not([data-theme])').trim());
    assert.equal(fallbackRules[index][2].trim(), rule[2].trim());
  }
  assert.ok(fallback.includes('--release-bg:#141d18'));
  assert.ok(fallback.includes('.wb-download-section'));
  assert.ok(fallback.includes('.wb-demo-disclaimer'));
  assert.ok(fallback.includes('.wb-checksum-details'));
});
