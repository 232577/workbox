// 管理员密钥只存在于 Worker Secret；浏览器使用短期 HttpOnly 会话。
const encoder = new TextEncoder();
const cookieName = '__Host-workbox_admin';
const lifetime = 8 * 60 * 60;
export const privateHeaders = {'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff', 'X-Robots-Tag':'noindex, nofollow'};
export const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function json(value, status=200, headers={}) {
  return Response.json(value,{status,headers:{...privateHeaders,...headers}});
}

export async function readJson(request) {
  if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/json')) throw Error('请使用 JSON 请求。');
  if (Number(request.headers.get('Content-Length')) > 1024) throw Error('请求过大。');
  const reader=request.body?.getReader();
  if (!reader) throw Error('请求不能为空。');
  let text='', size=0;
  const decoder=new TextDecoder();
  try {
    for (;;) {
      const {value,done}=await reader.read();
      if (done) break;
      size+=value.byteLength;
      if (size>1024) {await reader.cancel();throw Error('请求过大。');}
      text+=decoder.decode(value,{stream:true});
    }
    return JSON.parse(text+decoder.decode());
  } finally {reader.releaseLock();}
}

export function sameOrigin(request) {
  return request.headers.get('Origin') === new URL(request.url).origin
    && !['cross-site','none'].includes(request.headers.get('Sec-Fetch-Site'));
}

function configured(env) {return typeof env.STATS_ADMIN_KEY==='string' && env.STATS_ADMIN_KEY.length>=32;}
async function digest(value) {return new Uint8Array(await crypto.subtle.digest('SHA-256',encoder.encode(value)));}
async function equal(left,right) {
  const [a,b]=await Promise.all([digest(left),digest(right)]);
  let difference=0; for(let i=0;i<a.length;i++) difference|=a[i]^b[i];
  return difference===0;
}
const hex = bytes => Array.from(new Uint8Array(bytes), b=>b.toString(16).padStart(2,'0')).join('');
async function signingKey(secret) {
  return crypto.subtle.importKey('raw',encoder.encode(secret),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
}
async function sign(value,secret) {return hex(await crypto.subtle.sign('HMAC',await signingKey(secret),encoder.encode(value)));}

export async function authenticated(request,env,now=Date.now()) {
  if (!configured(env)) return false;
  const token=(request.headers.get('Cookie')||'').split(';').map(x=>x.trim()).find(x=>x.startsWith(cookieName+'='))?.slice(cookieName.length+1);
  const match=token?.match(/^v1\.(\d{10})\.([0-9a-f-]{36})\.([0-9a-f]{64})$/);
  if (!match || !uuid.test(match[2])) return false;
  const expiry=Number(match[1]);
  if(expiry<=Math.floor(now/1000) || expiry>Math.floor(now/1000)+lifetime) return false;
  const signature=Uint8Array.from(match[3].match(/../g),x=>parseInt(x,16));
  return crypto.subtle.verify('HMAC',await signingKey(env.STATS_ADMIN_KEY),signature,encoder.encode(`v1.${match[1]}.${match[2]}`));
}

export async function session(request,env) {
  if(!sameOrigin(request)) return json({error:'请求来源不匹配，请从统计页重试。'},403);
  if(request.method==='DELETE') return json({ok:true},200,{'Set-Cookie':`${cookieName}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0`});
  if(request.method!=='POST') return json({error:'不支持此方法。'},405,{Allow:'POST, DELETE'});
  if(!configured(env)) return json({error:'统计管理入口尚未配置。'},503);
  let body;
  try {body=await readJson(request);} catch {return json({error:'登录请求无效。'},400);}
  if(typeof body?.key!=='string' || !(await equal(body.key,env.STATS_ADMIN_KEY))) return json({error:'访问密钥不正确。'},401);
  const value=`v1.${Math.floor(Date.now()/1000)+lifetime}.${crypto.randomUUID()}`;
  const token=value+'.'+await sign(value,env.STATS_ADMIN_KEY);
  return json({ok:true},200,{'Set-Cookie':`${cookieName}=${token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${lifetime}`});
}

export async function metricsStub(request,env) {
  let namespace='site-v1';
  // 已认证验收使用单独对象，不把测试流量混入正式统计。
  const validation=request.headers.get('X-WorkBox-Validation');
  if(validation) {
    if(!uuid.test(validation) || !(await authenticated(request,env))) throw Error('invalid validation scope');
    namespace='validation-'+validation;
  }
  return env.METRICS.get(env.METRICS.idFromName(namespace));
}

export async function record(request,env,event) {
  if(!env.METRICS) return false;
  try {
    const response=await (await metricsStub(request,env)).fetch(new Request('https://metrics.internal/event',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(event)}));
    return response.ok;
  } catch {return false;}
}

export async function analyticsRoute(request,env) {
  const url=new URL(request.url);
  if(url.pathname==='/api/admin/session') return session(request,env);
  if(url.pathname==='/api/admin/stats') {
    if(!(await authenticated(request,env))) return json({error:'请先登录统计管理。'},401);
    if(request.method!=='GET') return json({error:'不支持此方法。'},405,{Allow:'GET'});
    const days=url.searchParams.get('days')||'30';
    if(!['7','30','90'].includes(days)) return json({error:'请选择7、30或90天。'},400);
    try {
      const response=await (await metricsStub(request,env)).fetch(new Request('https://metrics.internal/summary?days='+days));
      if(!response.ok) throw Error('unavailable');
      return json(await response.json());
    } catch {return json({error:'统计暂时不可用，请稍后重试。'},503);}
  }
  if(url.pathname==='/api/events') {
    if(request.method!=='POST') return json({error:'不支持此方法。'},405,{Allow:'POST'});
    if(!sameOrigin(request)) return json({error:'请求来源不匹配。'},403);
    let event;
    try {event=await readJson(request);} catch {return json({error:'请求无效。'},400);}
    if(!event || Object.keys(event).some(x=>!['type','id'].includes(x))
       || !['page_view','fallback_click'].includes(event.type) || !uuid.test(event.id)) return json({error:'事件无效。'},400);
    const saved=await record(request,env,event);
    return new Response(null,{status:saved?204:503,headers:privateHeaders});
  }
  return json({error:'未找到这个接口。'},404);
}
