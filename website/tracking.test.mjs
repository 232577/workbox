import test from 'node:test';
import assert from 'node:assert/strict';
import { handleRequest, release } from './worker.mjs';

const origin = 'https://workbox.ltxiangzi.dpdns.org';
const id = 'ca6602ce-34cf-49aa-833e-939cebf2c534';
function fixture() {
  const events = [];
  const env = {
    STATS_ADMIN_KEY: 'isolated-long-secret-for-tests-not-production',
    ASSETS: { fetch: async () => new Response('static-page') },
    METRICS: { idFromName: x => x, get: () => ({fetch: async request => {
      if (new URL(request.url).pathname === '/event') events.push(await request.json());
      return Response.json({ok:true, today:{page_views:0,downloads:0},total:{page_views:0,downloads:0},days:[]});
    }}) },
  };
  return {env, events};
}
const req = (path, init={}) => new Request(origin + path, init);
const post = value => ({method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(value)});
const upstream = async () => new Response('MZ', {headers:{'Content-Type':'application/octet-stream'}});

test('未登录不能读取统计，静态页不能绕过API权限', async () => {
  const {env} = fixture();
  const response = await handleRequest(req('/api/admin/stats?days=30'),env);
  assert.equal(response.status,401);
  assert.match(response.headers.get('Cache-Control'), /no-store/);
  assert.equal((await handleRequest(req('/stats/'),env)).status,200);
});

test('密钥登录产生安全cookie，篡改、退出和跨站请求受限', async () => {
  const {env} = fixture();
  assert.equal((await handleRequest(req('/api/admin/session',post({key:'wrong'})),env)).status,401);
  const login = await handleRequest(req('/api/admin/session',post({key:env.STATS_ADMIN_KEY})),env);
  assert.equal(login.status,200);
  const cookie = login.headers.get('Set-Cookie');
  for (const flag of ['HttpOnly','Secure','SameSite=Strict','Path=/']) assert.ok(cookie.includes(flag));
  const stats = await handleRequest(req('/api/admin/stats?days=30',{headers:{Cookie:cookie.split(';')[0]}}),env);
  assert.equal(stats.status,200);
  assert.equal((await handleRequest(req('/api/admin/stats',{headers:{Cookie:cookie.split(';')[0]+'x'}}),env)).status,401);
  const foreign = post({key:env.STATS_ADMIN_KEY}); foreign.headers.Origin = 'https://unrelated.invalid';
  assert.equal((await handleRequest(req('/api/admin/session',foreign),env)).status,403);
  const logout = await handleRequest(req('/api/admin/session',{method:'DELETE',headers:{Origin:origin,Cookie:cookie.split(';')[0]}}),env);
  assert.match(logout.headers.get('Set-Cookie'),/Max-Age=0/);
});

test('公开上报仅允许浏览和备用点击，不允许伪造下载或时间', async () => {
  const {env,events} = fixture();
  assert.equal((await handleRequest(req('/api/events',post({type:'page_view',id})),env)).status,204);
  assert.equal(events[0].type,'page_view');
  assert.equal((await handleRequest(req('/api/events',post({type:'download_start',id})),env)).status,400);
  assert.equal((await handleRequest(req('/api/events',post({type:'page_view',id,at:0})),env)).status,400);
  const foreign=post({type:'page_view',id}); foreign.headers.Origin='https://other.invalid';
  assert.equal((await handleRequest(req('/api/events',foreign),env)).status,403);
  assert.equal(events.length,1);
});

test('真实下载成功开始才上报，HEAD失败和后续分段不计入', async () => {
  const {env,events}=fixture();
  const path=`/download/${release.filename}?download=${id}`;
  assert.equal((await handleRequest(req(path),env,upstream)).status,200);
  assert.equal(events.length,1); assert.equal(events[0].type,'download_start'); assert.equal(events[0].id,id);
  await handleRequest(req(path,{method:'HEAD'}),env,async()=>new Response(null));
  await handleRequest(req(path),env,async()=>new Response('missing',{status:404}));
  await handleRequest(req(path,{headers:{Range:'bytes=50-99'}}),env,async()=>new Response('bytes',{status:206,headers:{'Content-Range':`bytes 50-99/${release.size}`}}));
  assert.equal(events.length,1);
});

test('无标识直接下载先跳转带标识，统计故障不阻断下载', async () => {
  const {env}=fixture();
  const path=`/download/${release.filename}`;
  const response=await handleRequest(req(path),env,upstream);
  assert.equal(response.status,307);
  assert.match(response.headers.get('Location'),/download=[a-f0-9-]{36}/);
  env.METRICS.get=()=>({fetch:async()=>{throw Error('isolated outage');}});
  assert.equal((await handleRequest(req(path+'?download='+id),env,upstream)).status,200);
});

test('生产下载响应不等待悬挂的统计服务', async () => {
  const {env}=fixture();
  env.METRICS.get=()=>({fetch:()=>new Promise(()=>{})});
  const pending=[];
  const response=await Promise.race([
    handleRequest(req(`/download/${release.filename}?download=${id}`),env,upstream,{waitUntil:p=>pending.push(p)}),
    new Promise(resolve=>setTimeout(()=>resolve(null),100)),
  ]);
  assert.equal(response?.status,200);
  assert.equal(pending.length,1);
});

test('未授权验收请求不会回退污染正式计数', async () => {
  const {env,events}=fixture();
  const response=await handleRequest(req(`/download/${release.filename}?download=${id}`,{
    headers:{'X-WorkBox-Validation':id},
  }),env,upstream);
  assert.equal(response.status,200);
  assert.equal(events.length,0);
});

test('静态资源关闭自动HTML重写后首页仍定位index.html', async () => {
  const env={ASSETS:{fetch:async request => new URL(request.url).pathname==='/index.html'
    ? new Response('home') : new Response(null,{status:404})}};
  const response=await handleRequest(req('/'),env);
  assert.equal(response.status,200);
  assert.equal(await response.text(),'home');
});
