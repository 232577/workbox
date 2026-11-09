import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { handleRequest, release, releases } from './worker.mjs';

const url = `https://workbox.ltxiangzi.dpdns.org/download/${release.filename}`;

function failedUpstream(status, headers = {}) {
  let cancelled = false;
  const response = new Response(new ReadableStream({ cancel() { cancelled = true; } }), { status, headers });
  return { response, cancelled: () => cancelled };
}

test('错误正文清理拒绝不阻断单次重试或最终502及416', async () => {
  function erroredResponse(status, headers = {}) {
    return new Response(new ReadableStream({ start(controller) {
      controller.error(new Error('isolated failed upstream stream'));
    } }), { status, headers });
  }
  for (const final of ['success', 'invalid', 'transient', 'range']) {
    let calls = 0;
    const response = await handleRequest(new Request(url), {}, async () => {
      calls++;
      if (calls === 1) return erroredResponse(503);
      if (final === 'success') return new Response('MZ12', { headers: { 'Content-Type': 'application/octet-stream' } });
      if (final === 'invalid') return erroredResponse(200, { 'Content-Type': 'text/html' });
      return erroredResponse(final === 'range' ? 416 : 504);
    });
    assert.equal(calls, 2);
    assert.equal(response.status, final === 'success' ? 200 : final === 'range' ? 416 : 502);
    if (final === 'success') assert.equal(await response.text(), 'MZ12');
    else if (final === 'range') assert.equal(response.headers.get('Content-Range'), `bytes */${release.size}`);
    else assert.equal(response.headers.has('Content-Disposition'), false);
  }
  let calls = 0;
  const response = await handleRequest(new Request(url), {}, async () => { calls++; return erroredResponse(404); });
  assert.equal(calls, 1);
  assert.equal(response.status, 502);
});

test('暂时性上游错误只用同一可信资产固定查询重试一次，并先取消错误正文', async () => {
  for (const status of [408, 429, 500, 502, 503, 504]) {
    const failure = failedUpstream(status, { 'Content-Type': 'text/html' });
    const calls = [];
    const response = await handleRequest(new Request(url + '?target=https://untrusted.invalid', {
      headers: { Cookie: 'private', Authorization: 'private', Range: 'bytes=0-3', 'If-Range': '"version"', 'X-Visitor': 'private' },
    }), {}, async (target, options) => {
      calls.push(target);
      assert.equal(options.method, 'GET');
      assert.equal(options.redirect, 'follow');
      assert.deepEqual([...options.headers.keys()].sort(), ['accept', 'if-range', 'range', 'user-agent']);
      assert.equal(options.headers.get('Range'), 'bytes=0-3');
      assert.equal(options.headers.get('If-Range'), '"version"');
      if (calls.length === 1) return failure.response;
      assert.equal(failure.cancelled(), true);
      return new Response('MZ12', { status: 206, headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': '4', 'Content-Range': `bytes 0-3/${release.size}` } });
    });
    assert.equal(response.status, 206);
    assert.equal(await response.text(), 'MZ12');
    const source = `https://github.com/232577/workbox/releases/download/${release.tag}/${release.filename}`;
    assert.deepEqual(calls, [source, source + '?download=1']);
  }
});

test('网络异常后可重试HEAD，最终失败最多两次且保留原备用链接', async () => {
  for (const recover of [false, true]) {
    let calls = 0;
    const response = await handleRequest(new Request(url, { method: 'HEAD' }), {}, async (target, options) => {
      calls++;
      assert.equal(options.method, 'HEAD');
      assert.equal(options.headers.has('Range'), false);
      if (calls === 1 || !recover) throw new TypeError('isolated network error');
      assert.ok(target.endsWith('?download=1'));
      return new Response(null, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(release.size) } });
    });
    assert.equal(calls, 2);
    assert.equal(response.status, recover ? 200 : 502);
    if (recover) assert.equal(await response.text(), '');
    else assert.match(await response.text(), new RegExp(`/releases/download/${release.tag}/${release.filename}`));
  }
});

test('永久错误和无效成功响应不重试，错误正文均取消', async () => {
  for (const [status, headers] of [
    [400, {}], [401, {}], [403, {}], [404, {}], [416, {}], [501, {}],
    [200, { 'Content-Type': 'text/html' }], [200, { 'Content-Length': '5' }], [206, {}],
  ]) {
    let calls = 0;
    const failure = failedUpstream(status, headers);
    const response = await handleRequest(new Request(url), {}, async () => { calls++; return failure.response; });
    assert.equal(calls, 1);
    assert.equal(response.status, status === 416 ? 416 : 502);
    assert.equal(failure.cancelled(), true);
  }
});

test('重试响应仍执行安全校验且不产生第三次请求', async () => {
  for (const [status, headers] of [
    [504, {}], [404, {}], [200, { 'Content-Type': 'text/html' }],
    [200, { 'Content-Length': '5' }], [206, {}],
  ]) {
    let calls = 0;
    const first = failedUpstream(503);
    const second = failedUpstream(status, headers);
    const response = await handleRequest(new Request(url), {}, async () => ++calls === 1 ? first.response : second.response);
    assert.equal(calls, 2);
    assert.equal(response.status, 502);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(response.headers.has('Content-Disposition'), false);
    assert.equal(first.cancelled(), true);
    assert.equal(second.cancelled(), true);
  }
});

test('重试成功仅记一次下载开始，失败不记统计', async () => {
  for (const recover of [false, true]) {
    const events = [], pending = [];
    const id = '99999999-9999-4999-8999-999999999999';
    const env = { METRICS: { idFromName: name => name, get: () => ({ fetch: async request => {
      events.push(await request.json()); return new Response('ok');
    } }) } };
    let calls = 0;
    const response = await handleRequest(new Request(`${url}?download=${id}`), env, async () => {
      calls++;
      return calls === 1 || !recover ? new Response('unavailable', { status: 503 }) : new Response('MZ12', { headers: { 'Content-Type': 'application/octet-stream' } });
    }, { waitUntil: promise => pending.push(promise) });
    await Promise.all(pending);
    assert.equal(calls, 2);
    assert.equal(response.status, recover ? 200 : 502);
    assert.deepEqual(events.map(({ type, id }) => ({ type, id })), recover ? [{ type: 'download_start', id }] : []);
    assert.equal(response.headers.get('Cache-Control'), recover ? 'private, no-store' : 'no-store');
  }
});

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
  assert.equal(requested.target, `https://github.com/232577/workbox/releases/download/${release.tag}/${release.filename}`);
  assert.equal(requested.options.headers.has('Cookie'), false);
  assert.equal(requested.options.headers.has('Authorization'), false);
  assert.equal(response.headers.get('Content-Disposition'), `attachment; filename="${release.filename}"`);
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

test('历史r5安装包和其前十五个旧版本目录及摘要保持不变', async () => {
  const index=releases.findIndex(item=>item.tag==='v1.0.0.20261103.5');
  assert.ok(index>=0);
  const release=releases[index];
  assert.equal(release.tag,'v1.0.0.20261103.5');
  assert.equal(release.filename,'WorkBox-1.0.0-Setup-x64-20261103-r5.exe');
  assert.equal(release.edition,'正式安装版');
  assert.equal(release.date,'2026-11-03');
  assert.equal(release.size,38425694);
  assert.equal(release.sha256,'7ededf3d1a844bd9fc595697650dd7d1f8e8867dcf0c600b74a389714c9f2179');
  assert.deepEqual(releases.slice(index+1).map(item => [item.tag,item.filename,item.size,item.sha256]), [
    ['v1.0.0.20261103.4','WorkBox-1.0.0-Setup-x64-20261103-r4.exe',38427656,'8ba1ca559be45ffe007cb4d2ff8571ac5227017f7735d568088697236610ae53'],
    ['v1.0.0.20261103.3','WorkBox-1.0.0-Setup-x64-20261103-r3.exe',38410914,'590d0337091c1aae91c945d3ec206f2e5fbff4fb102d30b7b42670733380a36f'],
    ['v1.0.0.20261103.2','WorkBox-1.0.0-Setup-x64-20261103-r2.exe',38413646,'2a0ecc51895e7986f43a0e257ffeab16fafe747daa3dcbb34fd0ef949e0a0943'],
    ['v1.0.0.20261103.1','WorkBox-1.0.0-Setup-x64-20261103-r1.exe',38416001,'82538102216e692820b7e104b31372e9ef2b36864096758bd45d7f81da27ccda'],
    ['v1.0.0.20261102','WorkBox-1.0.0-Setup-x64-20261102.exe',39784180,'7760e997ded567dd5c1aec4435f023b82b64664ae757e249568999aac4f690fc'],
    ['v1.0.0.20261030','WorkBox-1.0.0-Setup-x64-20261030.exe',39721105,'15d2d54cd8c43709f33f460358f04b01542bdc1f041d874b49b76c871326e767'],
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
