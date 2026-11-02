import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {handleRequest,release} from './worker.mjs';

const url='https://workbox.ltxiangzi.dpdns.org/updates/stable.json';
test('更新清单从同一已核验发布目录生成，不请求GitHub限流接口',async()=>{
  const unexpected=()=>{throw Error('update metadata must not fetch upstream or use assets')};
  const result=await handleRequest(new Request(url),{ASSETS:{fetch:unexpected}},unexpected);
  assert.equal(result.status,200);
  assert.equal(result.headers.get('Cache-Control'),'no-store');
  const item=await result.json();
  assert.equal(item.tag_name,release.tag);assert.equal(item.draft,false);assert.equal(item.prerelease,false);
  assert.equal(item.html_url,`https://github.com/232577/workbox/releases/tag/${release.tag}`);
  assert.deepEqual(item.assets,[{name:release.filename,size:release.size,state:'uploaded',digest:`sha256:${release.sha256}`,
    browser_download_url:`https://github.com/232577/workbox/releases/download/${release.tag}/${release.filename}`}]);
});
test('更新清单HEAD无主体且拒绝写请求',async()=>{
  const unexpected=()=>{throw Error('no external work')};
  const result=await handleRequest(new Request(url,{method:'HEAD'}),{ASSETS:{fetch:unexpected}},unexpected);
  assert.equal(result.status,200);assert.equal((await result.arrayBuffer()).byteLength,0);
  assert.equal((await handleRequest(new Request(url,{method:'POST'}),{},unexpected)).status,405);
});
test('更新清单路由经过Worker而不是静态404',async()=>{
  const config=JSON.parse(await readFile(new URL('./wrangler.jsonc',import.meta.url),'utf8'));
  assert.ok(config.assets.run_worker_first.includes('/updates/*'));
});
