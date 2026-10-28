import { analyticsRoute, record, uuid, privateHeaders } from './admin.mjs';
export { SiteMetrics } from './analytics.mjs';
// 固定版本的公开下载入口；不读取、转发访问者的登录信息。
export const releases = Object.freeze([Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced.20261028',
  edition: '增强版 · 首次引导与通用模式',
  date: '2026-10-28',
  filename: 'WorkBox-1.0.0-Enhanced-20261028-Setup-x64.exe',
  size: 35542595,
  sha256: '4eb62d51a2e00777e3e4ad393268b38bf3c6232df7a1b338f9af586cbf4c642b',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced.20261027',
  edition: '增强版',
  date: '2026-10-27',
  filename: 'WorkBox-1.0.0-Enhanced-20261027-Setup-x64.exe',
  size: 35531055,
  sha256: 'a01018e5d0b9c866ce77c33b26a142cec524f9dd2b9a42c125a50a82a29645c8',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0',
  date: '2026-10-26',
  filename: 'WorkBox-1.0.0-Setup-x64.exe',
  size: 35436529,
  sha256: 'ad5025a6ca868609cbff621ed2ee6225c635626d50a0e5b25eda357695a859d8',
})]);
export const release = releases[0];

const safeHeaders = { 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin' };

function unavailable(source) {
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>下载暂时不可用 · 工作箱</title><h1>下载暂时不可用</h1><p>请稍后重试，或使用 <a href="${source}">GitHub 备用下载</a>。</p><p><a href="/">返回工作箱下载页</a></p></html>`, {
    status: 502,
    headers: { ...safeHeaders, 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

export async function handleRequest(request, env, fetcher = fetch, context = null) {
  const url = new URL(request.url);
  const { pathname } = url;
  if(pathname.startsWith('/api/')) return analyticsRoute(request,env);
  if (!['GET', 'HEAD'].includes(request.method)) {
    return new Response('仅支持 GET 和 HEAD。', { status: 405, headers: { ...safeHeaders, Allow: 'GET, HEAD' } });
  }
  if (pathname === '/healthz') {
    return new Response(request.method === 'HEAD' ? null : JSON.stringify(release), {
      headers: { ...safeHeaders, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  }
  if(pathname==='/' || pathname==='/stats' || pathname==='/stats/') {
    url.pathname=pathname==='/' ? '/index.html' : '/stats.html';url.search='';
    const response=await env.ASSETS.fetch(new Request(url,request));
    return new Response(response.body,{status:response.status,headers:{...Object.fromEntries(response.headers),
      ...(pathname==='/' ? {} : privateHeaders)}});
  }
  if (!pathname.startsWith('/download/')) return env.ASSETS.fetch(request);
  const fileRelease = releases.find(item => pathname === `/download/${item.filename}`);
  if (!fileRelease) return new Response('未找到这个版本的安装包。', { status: 404, headers: safeHeaders });
  const source = `https://github.com/232577/workbox/releases/download/${fileRelease.tag}/${fileRelease.filename}`;
  const range = request.headers.get('Range');
  if (range && !/^bytes=(?:\d+-\d*|-\d+)$/.test(range)) {
    return new Response(null, { status: 416, headers: { ...safeHeaders, 'Content-Range': `bytes */${fileRelease.size}` } });
  }
  const downloadId=url.searchParams.get('download');
  if(env.METRICS && request.method==='GET' && !uuid.test(downloadId||'')) {
    url.search='';url.searchParams.set('download',crypto.randomUUID());
    return new Response(null,{status:307,headers:{...safeHeaders,'Cache-Control':'no-store',Location:url.toString()}});
  }
  const headers = new Headers({ Accept: 'application/octet-stream', 'User-Agent': 'WorkBox-Public-Downloads' });
  if (range) headers.set('Range', range);
  if (range && request.headers.has('If-Range')) headers.set('If-Range', request.headers.get('If-Range'));
  let upstream;
  try {
    upstream = await fetcher(source, { method: request.method, headers, redirect: 'follow' });
  } catch {
    return unavailable(source);
  }
  if (upstream.status === 416) {
    return new Response(null, { status: 416, headers: { ...safeHeaders, 'Content-Range': `bytes */${fileRelease.size}` } });
  }
  const length = upstream.headers.get('Content-Length');
  if (![200, 206].includes(upstream.status)
      || /text\/|json|xml/i.test(upstream.headers.get('Content-Type') || '')
      || (upstream.status === 200 && length !== null && Number(length) !== fileRelease.size)
      || (upstream.status === 206 && !upstream.headers.has('Content-Range'))) {
    await upstream.body?.cancel();
    return unavailable(source);
  }
  const output = new Headers({ ...safeHeaders,
    'Content-Type': 'application/octet-stream',
    'Content-Disposition': `attachment; filename="${fileRelease.filename}"`,
    'Accept-Ranges': 'bytes',
    'Cache-Control': env.METRICS ? 'private, no-store' : 'public, max-age=86400, immutable',
  });
  for (const key of ['Content-Length', 'Content-Range', 'ETag', 'Last-Modified']) {
    if (upstream.headers.has(key)) output.set(key, upstream.headers.get(key));
  }
  if(env.METRICS && request.method==='GET' && (!range || /^bytes=0-/.test(range))) {
    // 只记有效下载响应的开始；后续分段和同一标识重试由存储去重。
    const recording=record(request,env,{type:'download_start',id:downloadId});
    if(context?.waitUntil) context.waitUntil(recording);
    else await recording;
  }
  return new Response(request.method === 'HEAD' ? null : upstream.body, { status: upstream.status, headers: output });
}

export default {
  fetch(request, env, context) {
    return handleRequest(request, env, fetch, context);
  },
};
