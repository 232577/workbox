import { analyticsRoute, record, uuid, privateHeaders } from './admin.mjs';
export { SiteMetrics } from './analytics.mjs';
// 固定版本的公开下载入口；不读取、转发访问者的登录信息。
export const releases = Object.freeze([Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261110.3',
  edition: '正式安装版',
  date: '2026-11-10',
  filename: 'WorkBox-1.0.0-Setup-x64-20261110-r3.exe',
  size: 42283385,
  sha256: '9425720502effe013002b7a6e728862d1d81adb68cb52cdeefb9ad158197129d',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.7',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r7.exe',
  size: 42226464,
  sha256: '240a816e8c5049e028060994002a5907ba59c58762ac6ff10c89eb4ce8f2d855',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.6',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r6.exe',
  size: 42200403,
  sha256: 'fb731518fd5e9ddd83aa6ec2bf96f5e41a3b76bf92f5e1fc5cbdc7020a00f304',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.5',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r5.exe',
  size: 42195578,
  sha256: 'cebcb7550a040df1b6b594491188b17525f9bfa11ee8dd1ada54e2467ccdc6ba',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.4',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r4.exe',
  size: 40662142,
  sha256: '745931fb42979eda910f76a4bbf0a9462c9d32e95679b1776d2a5b4274cd4220',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.2',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r2.exe',
  size: 40639450,
  sha256: '4d6525f5c8fe336a6c81d67248cb37ae2f0a355337991a54392cc8a30acffa43',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261109.1',
  edition: '正式安装版',
  date: '2026-11-09',
  filename: 'WorkBox-1.0.0-Setup-x64-20261109-r1.exe',
  size: 40624926,
  sha256: 'a8a7f9ac597900b52adacb6f1a8188d25309b88abc5c40c0af77bad473727524',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261106.1',
  edition: '正式安装版',
  date: '2026-11-06',
  filename: 'WorkBox-1.0.0-Setup-x64-20261106-r1.exe',
  size: 40613745,
  sha256: 'bbdf096bb8929f5129b05df81387cb7a947fb5af15ec96badcbad7edd5f06969',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261105.3',
  edition: '正式安装版',
  date: '2026-11-05',
  filename: 'WorkBox-1.0.0-Setup-x64-20261105-r3.exe',
  size: 40609504,
  sha256: '533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261105.2',
  edition: '正式安装版',
  date: '2026-11-05',
  filename: 'WorkBox-1.0.0-Setup-x64-20261105-r2.exe',
  size: 40620146,
  sha256: 'b60fe41773d7781d79fb9ef06657240445f289837a461f30083fc8286f4af616',
}), Object.freeze({
  version: '1.0.0',
  tag: 'v1.0.0.20261105.1',
  edition: '正式安装版',
  date: '2026-11-05',
  filename: 'WorkBox-1.0.0-Setup-x64-20261105-r1.exe',
  size: 40622040,
  sha256: 'a468a966befb8863e132d3510991fc715f8aa6938d7e1115388a8c70ef1e4537',
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
const transientDownloadStatuses = new Set([408, 429, 500, 502, 503, 504]);

async function discardDownloadBody(response) {
  try {
    await response.body?.cancel();
  } catch {
    // Cleanup failure must not replace the retry or validation result.
  }
}

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
      body: `WorkBox ${release.version} 正式安装版 · ${release.date}。来源提交 3863d4c63a4851f46ef62aecf1a5b075309a131d。本次整合 Studio 与名单核对，恢复窗口位置与最大化状态，保持模态窗口优先和悬浮入口可用。全局快捷键默认关闭，可在工作设置中启用。r6 优化项目较多时的搜索、筛选和项目切换，减少重复刷新与 ZIP 列表查询。顶部横向导航提供“项目”和“今日工作量”，搜索和筛选入口位于项目清单上方。项目清单选择“待开发”后可点击“导出名单”，将全部未归档、未删除的待开发项目写入 Excel，只有“项目名称”一列，每个项目一行。搜索和筛选只影响当前清单。导出文件保存前复核目标位置，覆盖时校验已确认的文件。宽窗并排显示项目清单和详情。窄窗点击项目打开详情，点击“返回项目清单”回到原阅读位置。批次编号后显示待完成与总数。5 个项目中有 1 个进入待交付，显示“待完成4，共5”；1 个项目进入待交付，显示“待完成0，共1”。待完成只计待开发和开发中。选择批次可查看待交付、已交付明细。待完成按整批项目的当前状态统计，个人工作量单独统计。首次新增日期按项目最初进入工作箱的北京时间日期筛选，重复收件不改变日期。工作目录只保留当前文件，旧稿移到同一磁盘上的独立历史区。已有旧稿须明确选择后确认整理，不会按文件名自动清理。归档只包含当前文件，既有交付归档和记录保留。项目沿用四种原项目状态、原排序和一项目一行，一个项目可属于多个批次。交付页保留日期、搜索、各类型数量和全部交付表。从窗口底部或高级设置主动检查一次，有新版时选择“更新并重启”。程序下载校验、备份数据库，在原目录安装成功后重新打开。归档分类按最近有效交付的单项目文件数判断，新安装预设恰好 16 个文件归为“可运行”，其他已知数量归为“生成材料”，数量未知时保留原类型。新安装的自动分类默认关闭，升级沿用原配置，旧规则仍按“至少”比较。ZIP 自动交付的独立初始门槛为每项目 4 个实际文件。升级前请备份 data、工作和归档目录及全部已登记历史文稿目录（可能仍在原工作位置）。迁移后核验路径与历史文件可读性。安装包尚无商业代码签名。`,
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
  // Retry only transient transport failures, using a fixed query on this trusted asset.
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      upstream = await fetcher(attempt === 0 ? source : `${source}?download=1`, {
        method: request.method, headers, redirect: 'follow',
      });
    } catch {
      if (attempt === 0) continue;
      return unavailable(source);
    }
    if (attempt === 0 && transientDownloadStatuses.has(upstream.status)) {
      await discardDownloadBody(upstream);
      continue;
    }
    break;
  }
  if (upstream.status === 416) {
    await discardDownloadBody(upstream);
    return new Response(null, { status: 416, headers: { ...safeHeaders, 'Content-Range': `bytes */${fileRelease.size}` } });
  }
  const length = upstream.headers.get('Content-Length');
  if (![200, 206].includes(upstream.status)
      || /text\/|json|xml/i.test(upstream.headers.get('Content-Type') || '')
      || (upstream.status === 200 && length !== null && Number(length) !== fileRelease.size)
      || (upstream.status === 206 && !upstream.headers.has('Content-Range'))) {
    await discardDownloadBody(upstream);
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
