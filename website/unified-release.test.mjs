import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {handleRequest,release,releases} from './worker.mjs';

test('首页、两份README与稳定发布指向11月5日项目批次版，保留全部历史下载',async()=>{
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  const readme=await readFile(new URL('../README.md',import.meta.url),'utf8');
  const siteReadme=await readFile(new URL('./README.md',import.meta.url),'utf8');
  for(const text of [html,readme,siteReadme]){
    assert.ok(text.includes('v1.0.0.20261105.3'));
    assert.ok(text.includes('WorkBox-1.0.0-Setup-x64-20261105-r3.exe'));
    assert.ok(text.includes('533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a'));
    assert.ok(text.includes('6eb83f50ce3eba52df0da9439be3243afc47df26'));
  }
  for(const item of releases) assert.ok(html.includes(`/download/${item.filename}`));
  assert.deepEqual(release,{version:'1.0.0',tag:'v1.0.0.20261105.3',edition:'正式安装版',date:'2026-11-05',filename:'WorkBox-1.0.0-Setup-x64-20261105-r3.exe',size:40609504,sha256:'533387b71a3ae6ddbb313a3c3a793d8a46d65ed3640cae624f7e40154af5059a'});
  // 固定原20个目录项的完整字段，防止升级时误改旧包大小、摘要或路径。
  assert.equal(releases.length,21);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),'b14365f75280ddbae09349b0e3deff206f0886601fc0688cbcbdde4add4c1f29');
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
  assert.match(html,/截图来自 v1\.0\.0\.20261105\.3 匿名实机截图/);
  const metadata=await (await handleRequest(new Request('https://example.com/updates/stable.json'),{})).json();
  assert.equal(metadata.tag_name,release.tag);
  assert.equal(metadata.assets[0].size,40609504);
  assert.equal(metadata.assets[0].digest,`sha256:${release.sha256}`);
  for(const text of [html,readme,siteReadme,metadata.body]){
    for(const phrase of ['原项目状态','原排序','一项目一行','多个批次','全部交付表'])assert.ok(text.includes(phrase),phrase);
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

test('项目图来自最终构建，44项旧媒体逐项保留原构建归属',async()=>{
  const registry=JSON.parse(await readFile(new URL('./MEDIA.json',import.meta.url),'utf8'));
  const baseline=JSON.parse(await readFile(new URL('./fixtures/media-before-task-groups.json',import.meta.url),'utf8'));
  const oldFiles={};
  for(const [path,facts] of Object.entries(baseline)){
    assert.deepEqual(registry.files[path],facts,path);oldFiles[path]=registry.files[path];
  }
  assert.equal(Object.keys(registry.files).length,50);
  assert.equal(Object.keys(oldFiles).length,44);
  assert.equal(createHash('sha256').update(JSON.stringify(oldFiles)).digest('hex'),'c9469b55de034b9e13117f0ebe8fa0ca636988bcc1d77fb0957b873169c3b2b3');
  assert.equal(registry.latest_screenshot_exe_sha256,'3db86834172f225584338465ee245398c16c56a544902d10c200f4108a75bc1d');
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  assert.match(html,/页面截图来自 v1\.0\.0\.20261105\.3/);
  assert.doesNotMatch(html,/截图来自 v1\.0\.0\.20261104\.3/);
});
