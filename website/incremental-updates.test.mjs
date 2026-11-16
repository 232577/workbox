import test from 'node:test';
import assert from 'node:assert/strict';
import { releaseAssets, handleRequest, release, releases } from './worker.mjs';

const from = 'v1.0.0.20261113.2';
const to = 'v1.0.0.20261113.3';
const name = 'WorkBox-1.0.0-Delta-x64-20261113-r2-to-20261113-r3.wbdelta';
const url = `https://github.com/232577/workbox/releases/download/${to}/${name}`;
const delta = {name, size: 1024, sha256: 'a'.repeat(64), from_tag: from, to_tag: to, url, state: 'uploaded'};
const candidate = {version:'1.0.0',tag:to,filename:'WorkBox-1.0.0-Setup-x64-20261113-r3.exe',size:4096,sha256:'b'.repeat(64)};
const full = item => ({name:item.filename,size:item.size,state:'uploaded',digest:`sha256:${item.sha256}`,
  browser_download_url:`https://github.com/232577/workbox/releases/download/${item.tag}/${item.filename}`});

test('没有真实增量目录时完整资产保持旧客户端原样兼容', async () => {
  for (const item of releases) {
    assert.equal(item.deltas, undefined);
    assert.deepEqual(releaseAssets(item), [full(item)]);
  }
  const response = await handleRequest(new Request('https://example.com/updates/stable.json'), {});
  assert.deepEqual((await response.json()).assets, [full(release)]);
});

test('仅明确目录中的合法已上传增量追加GitHub标准资产，不修改原目录', () => {
  const item = {...candidate,deltas:[{...delta}]};
  const original = structuredClone(item);
  assert.deepEqual(releaseAssets(item), [full(item), {name,size:1024,state:'uploaded',digest:'sha256:'+'a'.repeat(64),browser_download_url:url}]);
  assert.deepEqual(item, original);
});

for (const [field,value] of [
  ['name','wrong.wbdelta'],['size',true],['size','1024'],['size',0],['size',1],['size',1.5],['size',4096],
  ['size',256*1024*1024+1],['sha256','A'.repeat(64)],['sha256','0'.repeat(63)],
  ['from_tag',to],['from_tag','v1.0.0.20261113.4'],['from_tag','v1.0.0.20260230'],
  ['from_tag','v1.0.0.20261113.0'],['to_tag','v1.0.0.20261113.4'],['state','new'],
  ['url',url.replace('232577/workbox','someone/workbox')],['url',url.replace('https:','http:')],
  ['url',url+'?download=1'],['url',url.replace('github.com','github.com.evil.invalid')],
]) test(`无效可选增量不会发布，也不阻断完整包：${field}=${String(value)}`, () => {
  assert.deepEqual(releaseAssets({...candidate,deltas:[{...delta,[field]:value}]}),[full(candidate)]);
});

test('可选目录重复、非数组、未知字段都关闭增量而保留完整资产', () => {
  for(const deltas of [null,{},[delta,delta],[{...delta,untrusted:true}]])
    assert.deepEqual(releaseAssets({...candidate,deltas}),[full(candidate)]);
});

test('无修订标签和跨年日期按真实日历及数值比较', () => {
  const old='v1.0.0.20261231', next='v1.0.0.20270101.1';
  const filename='WorkBox-1.0.0-Delta-x64-20261231-to-20270101-r1.wbdelta';
  const item={...candidate,tag:next,deltas:[{...delta,from_tag:old,to_tag:next,name:filename,
    url:`https://github.com/232577/workbox/releases/download/${next}/${filename}`}]};
  assert.equal(releaseAssets(item).length,2);
});
