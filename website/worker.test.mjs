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
  assert.equal(requested.target, 'https://github.com/232577/workbox/releases/download/v1.0.0.20261030/WorkBox-1.0.0-Setup-x64-20261030.exe');
  assert.equal(requested.options.headers.has('Cookie'), false);
  assert.equal(requested.options.headers.has('Authorization'), false);
  assert.match(response.headers.get('Content-Disposition'), /attachment; filename="WorkBox-1.0.0-Setup-x64-20261030.exe"/);
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

test('正式安装版使用独立标签与安装包，九个旧版本目录和摘要保持不变', async () => {
  assert.equal(release.tag,'v1.0.0.20261030');
  assert.equal(release.filename,'WorkBox-1.0.0-Setup-x64-20261030.exe');
  assert.equal(release.edition,'正式安装版');
  assert.equal(release.date,'2026-10-30');
  assert.equal(release.size,39721105);
  assert.equal(release.sha256,'15d2d54cd8c43709f33f460358f04b01542bdc1f041d874b49b76c871326e767');
  assert.equal(releases.length,10);
  assert.deepEqual(releases.slice(1).map(item => [item.tag,item.filename,item.size,item.sha256]), [
    ['v1.0.0-enhanced-types.20261029','WorkBox-1.0.0-Enhanced-Types-20261029-Setup-x64.exe',39706402,'f564009fc178b35580e93c854dda320633d7e6bade2524e51240d6455fe3566d'],
    ['v1.0.0-enhanced-sidebar.20261029','WorkBox-1.0.0-Enhanced-Sidebar-20261029-Setup-x64.exe',39685611,'7f04ad22dbb30dc61eaa1f2149ad6c65dcca7044e18b9b4fbb3ed21d11d01a5a'],
    ['v1.0.0-enhanced-layout.20261029','WorkBox-1.0.0-Enhanced-Layout-20261029-Setup-x64.exe',39687979,'45fc221a8f6e70c7269ed0e2bfceb5080f9e27641197c927c8db0617f82e2f17'],
    ['v1.0.0-enhanced-workbench.20261029','WorkBox-1.0.0-Enhanced-Workbench-20261029-Setup-x64.exe',39680913,'4e6dbe36faf3920ef637e0deca797950b596b72cc6f893bc9e78e302d0c5b548'],
    ['v1.0.0-enhanced-fluent.20261028','WorkBox-1.0.0-Enhanced-Fluent-20261028-Setup-x64.exe',39666409,'4c5c09b1e7c2d0599a8e3c9cd1887a5449559bbbcf82fe35ff7a41513d53a2f2'],
    ['v1.0.0-enhanced-ui.20261028','WorkBox-1.0.0-Enhanced-UI-20261028-Setup-x64.exe',37655551,'09a30ba343ad48f7f50e24f8423de44c7ecdcee385be260109d8ad237dc0afa3'],
    ['v1.0.0-enhanced.20261028','WorkBox-1.0.0-Enhanced-20261028-Setup-x64.exe',35542595,'4eb62d51a2e00777e3e4ad393268b38bf3c6232df7a1b338f9af586cbf4c642b'],
    ['v1.0.0-enhanced.20261027','WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe',35531055,'a01018e5d0b9c866ce77c33b26a142cec524f9dd2b9a42c125a50a82a29645c8'],
    ['v1.0.0','WorkBox-1.0.0-Setup-x64.exe',35436529,'ad5025a6ca868609cbff621ed2ee6225c635626d50a0e5b25eda357695a859d8'],
  ]);
  for (const item of releases) {
    let upstream;
    const response=await handleRequest(new Request(`https://workbox.ltxiangzi.dpdns.org/download/${item.filename}`,{method:'HEAD'}),{},async target=>{
      upstream=target;
      return new Response(null,{headers:{'Content-Length':String(item.size),'Content-Type':'application/octet-stream'}});
    });
    assert.equal(response.status,200);
    assert.equal(upstream,`https://github.com/232577/workbox/releases/download/${item.tag}/${item.filename}`);
  }
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
