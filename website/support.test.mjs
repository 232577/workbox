import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('./public/support.js', import.meta.url), 'utf8').catch(() => '');
const module = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const validate = module.validateSupportConfig;
const disabled = { enabled: false, methods: [] };
const valid = { enabled: true, methods: [{ type: 'wechat', image: '/support/wechat.png' }, { type: 'alipay', image: '/support/alipay.jpg' }] };

test('exports a pure support config validator', () => assert.equal(typeof validate, 'function'));
test('disabled, missing and empty configurations stay hidden', () => {
  for (const value of [null, undefined, {}, [], disabled, { ...valid, enabled: false }, { enabled: true, methods: [] }, { ...valid, enabled: 'true' }]) {
    assert.deepEqual(validate(value), disabled);
  }
});
test('accepts two local static image methods without changing the input', () => {
  const before = JSON.stringify(valid);
  assert.deepEqual(validate(valid), valid);
  assert.equal(JSON.stringify(valid), before);
  assert.deepEqual(validate({ enabled: true, methods: [{ type: 'alipay', image: '/support/pay-code.webp' }] }).methods.length, 1);
});
test('rejects remote, executable, encoded and traversal image paths', () => {
  for (const image of ['https://evil.test/qr.png', '//evil.test/qr.png', 'data:image/png;base64,AAA', '/support/../qr.png', '/support/%2e%2e/qr.png', '/support/qr.svg', '/support/qr.png?redirect=x', '/support/qr.png#x', '/support/sub/qr.png', '/support\\qr.png', '/support/<img>.png', '/support/qr.PNG', '/support/qr.png\n', `/support/${'x'.repeat(161)}.png`]) {
    assert.deepEqual(validate({ enabled: true, methods: [{ type: 'wechat', image }] }), disabled, image);
  }
});
test('rejects unrecognized labels, duplicate methods and excess configuration', () => {
  for (const methods of [[{ type: '<script>', image: '/support/pay.png' }], [{ type: ['wechat'], image: '/support/pay.png' }], [{ type: 'bank', image: '/support/pay.png' }], [valid.methods[0], valid.methods[0]], [...valid.methods, valid.methods[0]], [null], 'wechat']) {
    assert.deepEqual(validate({ enabled: true, methods }), disabled);
  }
});
test('shipped configuration is disabled with no payment images', async () => {
  assert.deepEqual(JSON.parse(await readFile(new URL('./public/support.json', import.meta.url), 'utf8')), disabled);
});
