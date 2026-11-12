import { siteHtml } from './public-documents.test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {handleRequest,releases} from './worker.mjs';

test('首页、文档与更新说明保留项目批次、全量导出与文件处理边界',async()=>{
  const html = siteHtml;
  const readme=await readFile(new URL('../README.md',import.meta.url),'utf8');
  const siteReadme=await readFile(new URL('./README.md',import.meta.url),'utf8');
  for(const item of releases) assert.ok(html.includes(`/download/${item.filename}`));
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
  const metadata=await (await handleRequest(new Request('https://example.com/updates/stable.json'),{})).json();
  for(const text of [html,readme,siteReadme,metadata.body]){
    for(const phrase of ['原项目状态','原排序','一项目一行','多个批次','全部交付表'])assert.ok(text.includes(phrase),phrase);
  }
  for(const text of [readme,siteReadme]){
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
    assert.match(text,/同名普通文档.*新稿保留标准名/);
    assert.match(text,/当前文件.*旧稿.*独立/);
    assert.match(text,/已有旧稿.*明确选择/);
    assert.match(text,/归档只包含当前文件/);
    assert.match(text,/旧版.*后缀.*再次拖入.*新规则/);
    assert.match(text,/与当前标准名内容相同.*不增加版本/);
    assert.match(text,/ZIP.*归档.*(?:规则|记录)不变/);
  }
});

test('44项旧媒体逐项保留原构建归属',async()=>{
  const registry=JSON.parse(await readFile(new URL('./MEDIA.json',import.meta.url),'utf8'));
  const baseline=JSON.parse(await readFile(new URL('./fixtures/media-before-task-groups.json',import.meta.url),'utf8'));
  const oldFiles={};
  for(const [path,facts] of Object.entries(baseline)){
    assert.deepEqual(registry.files[path],facts,path);oldFiles[path]=registry.files[path];
  }
  assert.equal(Object.keys(oldFiles).length,44);
  assert.equal(createHash('sha256').update(JSON.stringify(oldFiles)).digest('hex'),'c9469b55de034b9e13117f0ebe8fa0ca636988bcc1d77fb0957b873169c3b2b3');
});
