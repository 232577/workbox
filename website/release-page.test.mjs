import { documents } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { release, releases } from './worker.mjs';

const home = documents['/index.html'];
const guide = documents['/guide.html'];
const history = documents['/releases.html'];
const demo = await import('./public/demo.mjs').catch(() => ({}));

test('首页优先呈现真实界面和下载，体验流程移到独立指南', () => {
  assert.ok(home.indexOf('id="actual-ui"') < home.indexOf('id="overview"'));
  assert.ok(home.indexOf('id="overview"') < home.indexOf('id="download"'));
  assert.doesNotMatch(home, /id="workbox-demo"|id="try-workbox"|src="\/demo\.mjs/);
  assert.match(guide, /id="workbox-demo"/);
  assert.match(guide, /示例演示，不读取或上传真实文件/);
  assert.match(guide, /src="\/demo\.mjs/);
  assert.doesNotMatch(home + guide, /<input[^>]*type="file"/);
});

test('独立指南有可定位目录，首页链接直接到指南', () => {
  assert.match(home, /href="\/guide\.html(?:#[^"]*)?"/);
  assert.match(home, /href="\/releases\.html(?:#[^"]*)?"/);
  assert.match(guide, /id="guide"/);
  assert.match(guide, /aria-label="使用指南目录"[\s\S]*?href="#project-batches"/);
});

test('指南演示操作为原生按钮，状态可被辅助技术读取', () => {
  assert.match(guide, /<button[^>]*type="button"[^>]*data-demo-next/);
  assert.match(guide, /data-demo-status[^>]*aria-live="polite"/);
  assert.match(guide, /<noscript>[^<]*演示/);
});

test('独立历史版本页无脚本仍有所有真实下载链接，首页保留校验与签名说明', () => {
  assert.match(history, /<section[^>]*id="release-history"/);
  for (const item of releases) assert.ok(history.includes(`/download/${item.filename}`), item.filename);
  assert.ok(home.includes(release.sha256));
  assert.match(home, /安装包未做商业代码签名/);
});
test('示例依次说明复制、版本与人工交付，重复演示不累计工作量',()=>{
  assert.equal(typeof demo.demoState,'function');
  const states=[0,1,2,3,0].map(i=>demo.demoState(i));
  assert.deepEqual(states.map(s=>s.count),[0,0,0,1,0]);
  assert.deepEqual(states.map(s=>s.status),['待开发','待交付','待交付','已交付','待开发']);
});
test('示例状态拒绝越界输入，且每次返回独立数据',()=>{
  assert.equal(typeof demo.demoState,'function');
  for(const index of [-1,4,NaN,'1',null])assert.throws(()=>demo.demoState(index),RangeError);
  const first=demo.demoState(1);first.status='changed';
  assert.equal(demo.demoState(1).status,'待交付');
});
