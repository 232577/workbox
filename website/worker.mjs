import { analyticsRoute, record, uuid, privateHeaders } from './admin.mjs';
export { SiteMetrics } from './analytics.mjs';
// 固定版本的公开下载入口；不读取、转发访问者的登录信息。
export const releases = Object.freeze([Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261105.1',
  edition: '正式安装版',
  date: '2026-11-05',
  filename: 'WorkBox-1.0.0-Setup-x64-20261105-r1.exe',
  size: 40611355,
  sha256: '21ed65876e1ede6e3b2548b971823439c5095475f5fc581fa5a10620bc8e7485',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261104.3',
  edition: '正式安装版',
  date: '2026-11-04',
  filename: 'WorkBox-1.0.0-Setup-x64-20261104-r3.exe',
  size: 39895769,
  sha256: 'b4ed8821dea9a07aefde37dd5967940b6aa35e9768bbedd3e488db237b569019',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261104.2',
  edition: '正式安装版',
  date: '2026-11-04',
  filename: 'WorkBox-1.0.0-Setup-x64-20261104-r2.exe',
  size: 39888489,
  sha256: 'f369cdee7e7f86a730440ce433e92c9d07f665bd5e7cb46b15033447460194d5',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261103.5',
  edition: '正式安装版',
  date: '2026-11-03',
  filename: 'WorkBox-1.0.0-Setup-x64-20261103-r5.exe',
  size: 38425694,
  sha256: '7ededf3d1a844bd9fc595697650dd7d1f8e8867dcf0c600b74a389714c9f2179',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261103.4',
  edition: '正式安装版',
  date: '2026-11-03',
  filename: 'WorkBox-1.0.0-Setup-x64-20261103-r4.exe',
  size: 38427656,
  sha256: '8ba1ca559be45ffe007cb4d2ff8571ac5227017f7735d568088697236610ae53',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261103.3',
  edition: '正式安装版',
  date: '2026-11-03',
  filename: 'WorkBox-1.0.0-Setup-x64-20261103-r3.exe',
  size: 38410914,
  sha256: '590d0337091c1aae91c945d3ec206f2e5fbff4fb102d30b7b42670733380a36f',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261103.2',
  edition: '正式安装版',
  date: '2026-11-03',
  filename: 'WorkBox-1.0.0-Setup-x64-20261103-r2.exe',
  size: 38413646,
  sha256: '2a0ecc51895e7986f43a0e257ffeab16fafe747daa3dcbb34fd0ef949e0a0943',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261103.1',
  edition: '正式安装版',
  date: '2026-11-03',
  filename: 'WorkBox-1.0.0-Setup-x64-20261103-r1.exe',
  size: 38416001,
  sha256: '82538102216e692820b7e104b31372e9ef2b36864096758bd45d7f81da27ccda',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261102',
  edition: '正式安装版',
  date: '2026-11-02',
  filename: 'WorkBox-1.0.0-Setup-x64-20261102.exe',
  size: 39784180,
  sha256: '7760e997ded567dd5c1aec4435f023b82b64664ae757e249568999aac4f690fc',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261030',
  edition: '正式安装版',
  date: '2026-10-30',
  filename: 'WorkBox-1.0.0-Setup-x64-20261030.exe',
  size: 39721105,
  sha256: '15d2d54cd8c43709f33f460358f04b01542bdc1f041d874b49b76c871326e767',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-types.20261029',
  edition: '增强版 · 项目类型统计',
  date: '2026-10-29',
  filename: 'WorkBox-1.0.0-Enhanced-Types-20261029-Setup-x64.exe',
  size: 39706402,
  sha256: 'f564009fc178b35580e93c854dda320633d7e6bade2524e51240d6455fe3566d',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-sidebar.20261029',
  edition: '增强版 · 侧栏图标对齐',
  date: '2026-10-29',
  filename: 'WorkBox-1.0.0-Enhanced-Sidebar-20261029-Setup-x64.exe',
  size: 39685611,
  sha256: '7f04ad22dbb30dc61eaa1f2149ad6c65dcca7044e18b9b4fbb3ed21d11d01a5a',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-layout.20261029',
  edition: '增强版 · 导航布局修正',
  date: '2026-10-29',
  filename: 'WorkBox-1.0.0-Enhanced-Layout-20261029-Setup-x64.exe',
  size: 39687979,
  sha256: '45fc221a8f6e70c7269ed0e2bfceb5080f9e27641197c927c8db0617f82e2f17',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-workbench.20261029',
  edition: '增强版 · 工作台与项目管理',
  date: '2026-10-29',
  filename: 'WorkBox-1.0.0-Enhanced-Workbench-20261029-Setup-x64.exe',
  size: 39680913,
  sha256: '4e6dbe36faf3920ef637e0deca797950b596b72cc6f893bc9e78e302d0c5b548',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-fluent.20261028',
  edition: '增强版 · Fluent 控件与长名称阅读',
  date: '2026-10-28',
  filename: 'WorkBox-1.0.0-Enhanced-Fluent-20261028-Setup-x64.exe',
  size: 39666409,
  sha256: '4c5c09b1e7c2d0599a8e3c9cd1887a5449559bbbcf82fe35ff7a41513d53a2f2',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0-enhanced-ui.20261028',
  edition: '增强版 · 双主题界面',
  date: '2026-10-28',
  filename: 'WorkBox-1.0.0-Enhanced-UI-20261028-Setup-x64.exe',
  size: 37655551,
  sha256: '09a30ba343ad48f7f50e24f8423de44c7ecdcee385be260109d8ad237dc0afa3',
}), Object.freeze({
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

async function videoResponse(request, env) {
  // 静态资源路径在实际环境未声明范围能力，显式处理有限大小的教程视频。
  const sourceHeaders = new Headers(request.headers);
  sourceHeaders.delete('Range'); sourceHeaders.delete('If-Range');
  const source = await env.ASSETS.fetch(new Request(request.url, {method:request.method, headers:sourceHeaders}));
  if(source.status!==200) return source;
  const headers = new Headers(source.headers);
  headers.set('Accept-Ranges','bytes');
  let range=request.headers.get('Range');
  const ifRange=request.headers.get('If-Range');
  if(ifRange && ifRange!==headers.get('ETag') && ifRange!==headers.get('Last-Modified')) range=null;
  if(request.method==='HEAD' || !range) return new Response(request.method==='HEAD'?null:source.body,{headers});
  const bytes=await source.arrayBuffer();
  const size=bytes.byteLength;
  if(size>25*1024*1024) return new Response('视频文件过大。',{status:502,headers:safeHeaders});
  const match=/^bytes=(\d*)-(\d*)$/.exec(range);
  let start=0,end=size-1;
  if(match && match[1]) {start=Number(match[1]);if(match[2])end=Math.min(Number(match[2]),end);}
  else if(match && match[2]) start=Math.max(0,size-Number(match[2]));
  if(!match || (!match[1]&&!match[2]) || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)
      || start<0 || start>=size || end<start) {
    return new Response(null,{status:416,headers:{...safeHeaders,'Content-Range':`bytes */${size}`}});
  }
  headers.delete('Content-Encoding');
  headers.set('Content-Range',`bytes ${start}-${end}/${size}`);
  headers.set('Content-Length',String(end-start+1));
  return new Response(bytes.slice(start,end+1),{status:206,headers});
}

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
  if (pathname === '/updates/stable.json') {
    const metadata = {
      tag_name: release.tag, draft: false, prerelease: false,
      html_url: `https://github.com/232577/workbox/releases/tag/${release.tag}`,
      body: `WorkBox ${release.version} 正式安装版 · ${release.date}。来源提交 8240b1077673d62119b9a21130c48020a8d327bd。任务组保留原消息和固定编号，材料就绪与手动交接分别记录；Excel 导出四张工作表，支持导出本组；归档分类按最近有效交付预览和配置。任务数不计入项目首交工作量。主工作台采用平面双栏，操作跟随项目；统一按钮、输入与状态筛选控件，项目队列使用透明背景融入工作区，文件与目录使用统一图标，深色外观采用中性炭灰层次；日期输入更清晰，交付明细可拖高及放大。普通文件先建项后，不同文字批次的唯一同名项目沿用原身份；多同名或受保护状态仍需核对。同名普通文档再次收录时，新稿保留标准名，旧稿加内容摘要后缀，后缀已占用时加序号。旧版（包括 v1.0.0.20261104.2）已给新稿添加后缀的情况，再次拖入该新稿即可按新规则收录；与当前标准名内容相同则直接复用，不增加版本。来源文件留在原处，ZIP 及历史材料归档规则不变，启动或安装不会批量改名旧文档。保留材料检查、项目状态、交付历史和文件。升级前请备份数据；安装时保存数据库恢复点。安装包尚无商业代码签名。`,
      assets: [{name: release.filename, size: release.size, state: 'uploaded',
        digest: `sha256:${release.sha256}`,
        browser_download_url: `https://github.com/232577/workbox/releases/download/${release.tag}/${release.filename}`}],
    };
    return new Response(request.method === 'HEAD' ? null : JSON.stringify(metadata), {
      headers: {...safeHeaders, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'},
    });
  }
  if (pathname === '/healthz') {
    return new Response(request.method === 'HEAD' ? null : JSON.stringify(release), {
      headers: { ...safeHeaders, 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
    });
  }
  if(pathname.startsWith('/media/') && pathname.endsWith('.mp4')) return videoResponse(request,env);
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
