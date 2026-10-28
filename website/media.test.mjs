import test from 'node:test';
import assert from 'node:assert/strict';
import {handleRequest} from './worker.mjs';
import {readFile} from 'node:fs/promises';

const url='https://workbox.ltxiangzi.dpdns.org/media/workbox-quickstart-20261028.mp4';
const bytes=Uint8Array.from({length:20},(_,i)=>i);
function env(){return {ASSETS:{fetch:async request=>new Response(request.method==='HEAD'?null:bytes,{headers:{'Content-Type':'video/mp4','Content-Length':'20',ETag:'"one"'}})}};}
test('视频完整响应声明字节范围，前端可拖动进度',async()=>{
  const response=await handleRequest(new Request(url),env());
  assert.equal(response.status,200);
  assert.equal(response.headers.get('Accept-Ranges'),'bytes');
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes);
});
test('视频范围与后缀请求返回准确206，不返回整段冒充片段',async()=>{
  for(const [range,start,end] of [['bytes=3-7',3,7],['bytes=15-',15,19],['bytes=-4',16,19]]){
    const response=await handleRequest(new Request(url,{headers:{Range:range}}),env());
    assert.equal(response.status,206);
    assert.equal(response.headers.get('Content-Range'),`bytes ${start}-${end}/20`);
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()),bytes.slice(start,end+1));
  }
});
test('无效视频范围拒绝，过期If-Range回退完整响应，HEAD没有主体',async()=>{
  for(const range of ['bytes=20-','bytes=4-2','bytes=0-1,3-4','bytes=-0'])assert.equal((await handleRequest(new Request(url,{headers:{Range:range}}),env())).status,416);
  assert.equal((await handleRequest(new Request(url,{headers:{Range:'bytes=3-5','If-Range':'"old"'}}),env())).status,200);
  const head=await handleRequest(new Request(url,{method:'HEAD'}),env());
  assert.equal(head.headers.get('Accept-Ranges'),'bytes');
  assert.equal((await head.arrayBuffer()).byteLength,0);
});
test('视频请求先经过Worker以应用范围逻辑',async()=>{
  const config=JSON.parse(await readFile(new URL('./wrangler.jsonc',import.meta.url),'utf8'));
  assert.ok(config.assets.run_worker_first.includes('/media/*'));
});
