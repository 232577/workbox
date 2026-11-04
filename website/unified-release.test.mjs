import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {handleRequest,release,releases} from './worker.mjs';

test('首页、两份README与稳定发布指向11月4日r2，保留全部历史下载',async()=>{
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  const readme=await readFile(new URL('../README.md',import.meta.url),'utf8');
  const siteReadme=await readFile(new URL('./README.md',import.meta.url),'utf8');
  for(const text of [html,readme,siteReadme]){
    assert.ok(text.includes('v1.0.0.20261104.2'));
    assert.ok(text.includes('WorkBox-1.0.0-Setup-x64-20261104-r2.exe'));
    assert.ok(text.includes('f369cdee7e7f86a730440ce433e92c9d07f665bd5e7cb46b15033447460194d5'));
    assert.ok(text.includes('34b4be852e7fc87825c8ac309b624e48422e3c3a'));
  }
  for(const item of releases) assert.ok(html.includes(`/download/${item.filename}`));
  assert.deepEqual(release,{version:'1.0.0',tag:'v1.0.0.20261104.2',edition:'正式安装版',date:'2026-11-04',filename:'WorkBox-1.0.0-Setup-x64-20261104-r2.exe',size:39888489,sha256:'f369cdee7e7f86a730440ce433e92c9d07f665bd5e7cb46b15033447460194d5'});
  // 固定原16个目录项的完整字段，防止升级时误改旧包大小、摘要或路径。
  assert.equal(releases.length,17);
  assert.equal(createHash('sha256').update(JSON.stringify(releases.slice(1))).digest('hex'),'4512ac629d098c4a92d88778d06336587e8b8f1b019f52c0ccea147fcb34865f');
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
  assert.match(html,/截图来自 v1\.0\.0\.20261104\.2 匿名实机截图/);
  const metadata=await (await handleRequest(new Request('https://example.com/updates/stable.json'),{})).json();
  assert.equal(metadata.tag_name,release.tag);
  assert.equal(metadata.assets[0].size,39888489);
  assert.equal(metadata.assets[0].digest,`sha256:${release.sha256}`);
  for(const text of [html,readme,siteReadme,metadata.body]){
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
  }
});
