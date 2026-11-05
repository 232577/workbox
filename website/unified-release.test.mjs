import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {handleRequest,release,releases} from './worker.mjs';

test('首页、两份README与稳定发布指向11月5日任务组版，保留全部历史下载',async()=>{
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  const readme=await readFile(new URL('../README.md',import.meta.url),'utf8');
  const siteReadme=await readFile(new URL('./README.md',import.meta.url),'utf8');
  for(const text of [html,readme,siteReadme]){
    assert.ok(text.includes('v1.0.0.20261105.2'));
    assert.ok(text.includes('WorkBox-1.0.0-Setup-x64-20261105-r2.exe'));
    assert.ok(text.includes('b60fe41773d7781d79fb9ef06657240445f289837a461f30083fc8286f4af616'));
    assert.ok(text.includes('b2724ec9073f3d78126d6fa0e049c5c000045cd5'));
  }
  for(const item of releases) assert.ok(html.includes(`/download/${item.filename}`));
  assert.deepEqual(release,{version:'1.0.0',tag:'v1.0.0.20261105.2',edition:'正式安装版',date:'2026-11-05',filename:'WorkBox-1.0.0-Setup-x64-20261105-r2.exe',size:40620146,sha256:'b60fe41773d7781d79fb9ef06657240445f289837a461f30083fc8286f4af616'});
  // 固定原19个目录项的完整字段，防止升级时误改旧包大小、摘要或路径。
  assert.equal(releases.length,20);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),'7ebbfa67ef879ced8e673608ca8be8fdf0ee2e075c1ce196a38b34acd832aa11');
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
  assert.match(html,/截图来自 v1\.0\.0\.20261105\.1 匿名实机截图/);
  const metadata=await (await handleRequest(new Request('https://example.com/updates/stable.json'),{})).json();
  assert.equal(metadata.tag_name,release.tag);
  assert.equal(metadata.assets[0].size,40620146);
  assert.equal(metadata.assets[0].digest,`sha256:${release.sha256}`);
  for(const text of [html,readme,siteReadme,metadata.body]){
    for(const phrase of ['任务组','材料就绪','手动交接','四张工作表','归档分类'])assert.ok(text.includes(phrase),phrase);
    assert.match(text,/平面双栏/);
    assert.match(text,/统一按钮、输入与状态筛选控件/);
    assert.match(text,/项目队列使用透明背景融入工作区/);
    assert.match(text,/文件与目录使用统一图标/);
    assert.match(text,/中性炭灰层次/);
    assert.match(text,/跟随项目/);
    assert.match(text,/日期输入/);
    assert.match(text,/交付明细.*拖高.*放大/);
    assert.match(text,/普通文件.*先建项/);
    assert.match(text,/不同文字批次.*唯一同名.*原身份/);
    assert.match(text,/多同名.*受保护状态.*核对/);
    assert.match(text,/同名普通文档.*新稿保留标准名.*旧稿加内容摘要后缀/);
    assert.match(text,/旧版.*后缀.*再次拖入.*新规则/);
    assert.match(text,/与当前标准名内容相同.*不增加版本/);
    assert.match(text,/ZIP.*归档.*规则不变/);
  }
});

test('任务组图来自最终构建，44项旧媒体逐项保留原构建归属',async()=>{
  const registry=JSON.parse(await readFile(new URL('./MEDIA.json',import.meta.url),'utf8'));
  const baseline=JSON.parse(await readFile(new URL('./fixtures/media-before-task-groups.json',import.meta.url),'utf8'));
  const oldFiles={};
  for(const [path,facts] of Object.entries(baseline)){
    assert.deepEqual(registry.files[path],facts,path);oldFiles[path]=registry.files[path];
  }
  assert.equal(Object.keys(registry.files).length,46);
  assert.equal(Object.keys(oldFiles).length,44);
  assert.equal(createHash('sha256').update(JSON.stringify(oldFiles)).digest('hex'),'c9469b55de034b9e13117f0ebe8fa0ca636988bcc1d77fb0957b873169c3b2b3');
  assert.equal(registry.latest_screenshot_exe_sha256,'1ed9cffe21ba9f366064224cf14f04b3b49c5983d8b6679e15d1b9c1fd6c05e1');
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/页面截图来自 v1\.0\.0\.20261105\.1/);
  assert.doesNotMatch(html,/截图来自 v1\.0\.0\.20261104\.3/);
});
