// 固定版本的公开下载入口；不读取、转发访问者的登录信息。
export const release = Object.freeze({
  version: '1.0.0',
  date: '2026-10-26',
  filename: 'WorkBox-1.0.0-Setup-x64.exe',
  size: 35436529,
  sha256: 'ad5025a6ca868609cbff621ed2ee6225c635626d50a0e5b25eda357695a859d8',
});

const source = `https://github.com/232577/workbox/releases/download/v${release.version}/${release.filename}`;
const downloadPath = `/download/${release.filename}`;
const safeHeaders = { 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin' };

function unavailable() {
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>下载暂时不可用 · 工作箱</title><h1>下载暂时不可用</h1><p>请稍后重试，或使用 <a href="${source}">GitHub 备用下载</a>。</p><p><a href="/">返回工作箱下载页</a></p></html>`, {
    status: 502,
    headers: { ...safeHeaders, 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

export async function handleRequest(request, env, fetcher = fetch) {
  const { pathname } = new URL(request.url);
  if (!['GET', 'HEAD'].includes(request.method)) {
    return new Response('仅支持 GET 和 HEAD。', { status: 405, headers: { ...safeHeaders, Allow: 'GET, HEAD' } });
  }
  if (pathname === '/healthz') {
    return new Response(request.method === 'HEAD' ? null : JSON.stringify(release), {
      headers: { ...safeHeaders, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  }
  if (!pathname.startsWith('/download/')) return env.ASSETS.fetch(request);
  if (pathname !== downloadPath) return new Response('未找到这个版本的安装包。', { status: 404, headers: safeHeaders });
  const range = request.headers.get('Range');
  if (range && !/^bytes=(?:\d+-\d*|-\d+)$/.test(range)) {
    return new Response(null, { status: 416, headers: { ...safeHeaders, 'Content-Range': `bytes */${release.size}` } });
  }
  const headers = new Headers({ Accept: 'application/octet-stream', 'User-Agent': 'WorkBox-Public-Downloads' });
  if (range) headers.set('Range', range);
  if (range && request.headers.has('If-Range')) headers.set('If-Range', request.headers.get('If-Range'));
  let upstream;
  try {
    upstream = await fetcher(source, { method: request.method, headers, redirect: 'follow' });
  } catch {
    return unavailable();
  }
  if (upstream.status === 416) {
    return new Response(null, { status: 416, headers: { ...safeHeaders, 'Content-Range': `bytes */${release.size}` } });
  }
  const length = upstream.headers.get('Content-Length');
  if (![200, 206].includes(upstream.status)
      || /text\/|json|xml/i.test(upstream.headers.get('Content-Type') || '')
      || (upstream.status === 200 && length !== null && Number(length) !== release.size)
      || (upstream.status === 206 && !upstream.headers.has('Content-Range'))) {
    await upstream.body?.cancel();
    return unavailable();
  }
  const output = new Headers({ ...safeHeaders,
    'Content-Type': 'application/octet-stream',
    'Content-Disposition': `attachment; filename="${release.filename}"`,
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'public, max-age=86400, immutable',
  });
  for (const key of ['Content-Length', 'Content-Range', 'ETag', 'Last-Modified']) {
    if (upstream.headers.has(key)) output.set(key, upstream.headers.get(key));
  }
  return new Response(request.method === 'HEAD' ? null : upstream.body, { status: upstream.status, headers: output });
}

export default {
  fetch(request, env) {
    return handleRequest(request, env);
  },
};
