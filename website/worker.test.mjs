import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { handleRequest, release, releases } from './worker.mjs';

const url = `https://workbox.ltxiangzi.dpdns.org/download/${release.filename}`;

test('Cloudflare入口接受ExecutionContext，不把它当成上游函数', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(new Uint8Array([77, 90]));
  try {
    assert.equal((await worker.fetch(new Request(url), {}, { waitUntil() {} })).status, 200);
  } finally {
    globalThis.fetch = original;
  }
});

test('公开下载固定版本并流式保留文件字节，不转发访问者凭据', async () => {
  let requested;
  const response = await handleRequest(new Request(url, { headers: { Cookie: 'visitor=private', Authorization: 'private' } }), {}, async (target, options) => {
    requested = { target, options };
    return new Response(new Uint8Array([77, 90, 1, 2]), { headers: { 'Content-Type': 'application/octet-stream' } });
  });
  assert.equal(response.status, 200);
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [77, 90, 1, 2]);
  assert.equal(requested.target, 'https://github.com/232577/workbox/releases/download/v1.0.0-enhanced.20261027/WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe');
  assert.equal(requested.options.headers.has('Cookie'), false);
  assert.equal(requested.options.headers.has('Authorization'), false);
  assert.match(response.headers.get('Content-Disposition'), /attachment; filename="WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe"/);
});

test('断点续传保留206和Content-Range', async () => {
  const response = await handleRequest(new Request(url, { headers: { Range: 'bytes=0-3' } }), {}, async (_, options) => {
    assert.equal(options.headers.get('Range'), 'bytes=0-3');
    return new Response('MZ12', { status: 206, headers: { 'Content-Type': 'application/octet-stream', 'Content-Range': `bytes 0-3/${release.size}`, 'Content-Length': '4' } });
  });
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('Content-Range'), `bytes 0-3/${release.size}`);
  assert.equal(await response.text(), 'MZ12');
});

test('HEAD只读取下载信息', async () => {
  const response = await handleRequest(new Request(url, { method: 'HEAD' }), {}, async (_, options) => {
    assert.equal(options.method, 'HEAD');
    return new Response(null, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(release.size) } });
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Length'), String(release.size));
  assert.equal(await response.text(), '');
});

test('上游返回HTML或错误时明确失败，不将网页当成安装包', async () => {
  for (const upstream of [new Response('missing', { status: 404 }), new Response('<html>error</html>', { headers: { 'Content-Type': 'text/html' } }), new Response('short', { headers: { 'Content-Length': '5' } })]) {
    const response = await handleRequest(new Request(url), {}, async () => upstream);
    assert.equal(response.status, 502);
    assert.equal(response.headers.get('Content-Disposition'), null);
    assert.match(await response.text(), /GitHub/);
  }
});

test('不接受任意下载地址或写入请求', async () => {
  const unexpected = () => { throw new Error('不应请求上游'); };
  assert.equal((await handleRequest(new Request(url.replace(release.filename, 'other.exe')), {}, unexpected)).status, 404);
  assert.equal((await handleRequest(new Request(url, { method: 'POST' }), {}, unexpected)).status, 405);
  assert.equal((await handleRequest(new Request(url, { headers: { Range: 'bytes=0-2,5-7' } }), {}, unexpected)).status, 416);
});

test('版本信息公开且页面回退到静态资源', async () => {
  const health = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/healthz'), {}, () => { throw new Error('不应请求上游'); });
  assert.deepEqual(await health.json(), release);
  const home = await handleRequest(new Request('https://workbox.ltxiangzi.dpdns.org/'), { ASSETS: { fetch: async () => new Response('workbox page') } });
  assert.equal(await home.text(), 'workbox page');
});

test('增强版独立标签与旧版安装入口同时保留', async () => {
  assert.equal(release.tag,'v1.0.0-enhanced.20261027');
  assert.equal(release.filename,'WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe');
  assert.equal(releases.length,2);
  const oldUrl='https://workbox.ltxiangzi.dpdns.org/download/WorkBox-1.0.0-Setup-x64.exe';
  let requested;
  const old=await handleRequest(new Request(oldUrl,{method:'HEAD'}),{},async (target)=>{
    requested=target;
    return new Response(null,{headers:{'Content-Length':'35436529','Content-Type':'application/octet-stream'}});
  });
  assert.equal(requested,'https://github.com/232577/workbox/releases/download/v1.0.0/WorkBox-1.0.0-Setup-x64.exe');
  assert.equal(old.status,200);
  assert.equal(old.headers.get('Content-Length'),'35436529');
  assert.match(old.headers.get('Content-Disposition'),/WorkBox-1.0.0-Setup-x64.exe/);
});
