import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {release,releases} from './worker.mjs';

test('首页、README与稳定发布指向同一整合包，保留r4和r3下载',async()=>{
  const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
  const readme=await readFile(new URL('../README.md',import.meta.url),'utf8');
  for(const text of [html,readme]){
    assert.ok(text.includes('v1.0.0.20261103.5'));
    assert.ok(text.includes('WorkBox-1.0.0-Setup-x64-20261103-r5.exe'));
    assert.ok(text.includes('7ededf3d1a844bd9fc595697650dd7d1f8e8867dcf0c600b74a389714c9f2179'));
    assert.ok(text.includes('812304b'));
  }
  for(const revision of [3,4]) assert.ok(html.includes(`/download/WorkBox-1.0.0-Setup-x64-20261103-r${revision}.exe`));
  assert.equal(release.tag,'v1.0.0.20261103.5');
  assert.equal(new Set(releases.map(r=>r.tag)).size,releases.length);
});
