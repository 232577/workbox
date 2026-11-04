import test from 'node:test';
import assert from 'node:assert/strict';
import {handleRequest} from './worker.mjs';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

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

test('11月4日r2匿名截图来自同一冻结构建，保留全部旧媒体来源',async()=>{
  const registry=JSON.parse(await readFile(new URL('./MEDIA.json',import.meta.url),'utf8'));
  const exe='021f59a84ee0a78a823a5eef54b5804803c12ec30f45714984d8bb4206d01352';
  assert.equal(registry.latest_screenshot_exe_sha256,exe);
  const oldFiles={...registry.files};
  for(const [theme,size,sha256] of [
    ['light',72805,'e035b4754584c8524ed8d646616ca080bb1bc3530374f63cab4c2d7bb852beb9'],
    ['dark',73582,'b8c34724ce9d1c0ffb9fe3bdf246835b3daa426bcbab19d7d0d968aa20e5342d'],
  ]){
    const path=`public/media/workbox-desktop-${theme}-20261104-r2.png`;
    assert.deepEqual(registry.files[path],{size,sha256,source_exe_sha256:exe,source_commit:'34b4be852e7fc87825c8ac309b624e48422e3c3a'});
    const png=await readFile(new URL(`./${path}`,import.meta.url));
    assert.equal(png.length,size);
    assert.equal(createHash('sha256').update(png).digest('hex'),sha256);
    assert.equal(png.readUInt32BE(16),1186);
    assert.equal(png.readUInt32BE(20),843);
    delete oldFiles[path];
  }
  assert.equal(Object.keys(oldFiles).length,42);
  assert.equal(createHash('sha256').update(JSON.stringify(oldFiles)).digest('hex'),'d4660da314aba88a05d9c8dc87dae08e5dd09c38c8a18aa7c5c83449e56baddb');
  assert.equal(registry.source_exe_sha256,'ce9e986d53fd0129c905e4058f7a8bfe3b67df4b0e1d6a8310d42dcd8a114890');
  assert.equal(registry.active_video,'public/media/workbox-official-20261030.mp4');
});
