import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {release, releases} from './worker.mjs';

const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
const demo=await import('./public/demo.mjs').catch(()=>({}));

test('首屏为原创流程演示，真实界面另有明确入口',()=>{
  assert.match(html,/id="workbox-demo"/);
  assert.match(html,/示例演示，不读取或上传真实文件/);
  assert.match(html,/id="actual-ui"/);
  assert.match(html,/href="#actual-ui"/);
  assert.match(html,/src="\/demo.mjs/);
  assert.doesNotMatch(html,/<input[^>]*type="file"/);
});
test('演示操作为原生按钮，状态可被辅助技术读取',()=>{
  assert.match(html,/<button[^>]*type="button"[^>]*data-demo-next/);
  assert.match(html,/data-demo-status[^>]*aria-live="polite"/);
  assert.match(html,/<noscript>[^<]*演示/);
});
test('历史版本折叠呈现但无脚本仍有所有真实下载链接',()=>{
  assert.match(html,/<details[^>]*id="release-history"/);
  for(const item of releases)assert.ok(html.includes(`/download/${item.filename}`),item.filename);
  assert.ok(html.includes(release.sha256));
  assert.match(html,/安装包未做商业代码签名/);
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
