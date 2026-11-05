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
    assert.ok(text.includes('v1.0.0.20261105.1'));
    assert.ok(text.includes('WorkBox-1.0.0-Setup-x64-20261105-r1.exe'));
    assert.ok(text.includes('21ed65876e1ede6e3b2548b971823439c5095475f5fc581fa5a10620bc8e7485'));
    assert.ok(text.includes('8240b1077673d62119b9a21130c48020a8d327bd'));
  }
  for(const item of releases) assert.ok(html.includes(`/download/${item.filename}`));
  assert.deepEqual(release,{version:'1.0.0',tag:'v1.0.0.20261105.1',edition:'正式安装版',date:'2026-11-05',filename:'WorkBox-1.0.0-Setup-x64-20261105-r1.exe',size:40611355,sha256:'21ed65876e1ede6e3b2548b971823439c5095475f5fc581fa5a10620bc8e7485'});
  // 固定原18个目录项的完整字段，防止升级时误改旧包大小、摘要或路径。
  assert.equal(releases.length,19);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),'1b5712f062ff03104c55336e4e194ae119845ed900f11c0be1b37e267ce64ce0');
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
  assert.match(html,/截图来自 v1\.0\.0\.20261104\.2 匿名实机截图/);
  const metadata=await (await handleRequest(new Request('https://example.com/updates/stable.json'),{})).json();
  assert.equal(metadata.tag_name,release.tag);
  assert.equal(metadata.assets[0].size,40611355);
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

test('r3后端规则升级完整保留44项真实媒体及r2构建归属',async()=>{
  const registry=JSON.parse(await readFile(new URL('./MEDIA.json',import.meta.url),'utf8'));
  assert.equal(Object.keys(registry.files).length,44);
  assert.equal(createHash('sha256').update(JSON.stringify(registry.files)).digest('hex'),'c9469b55de034b9e13117f0ebe8fa0ca636988bcc1d77fb0957b873169c3b2b3');
  assert.equal(registry.latest_screenshot_exe_sha256,'021f59a84ee0a78a823a5eef54b5804803c12ec30f45714984d8bb4206d01352');
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/页面截图来自 v1\.0\.0\.20261104\.2/);
  assert.doesNotMatch(html,/截图来自 v1\.0\.0\.20261104\.3/);
});
