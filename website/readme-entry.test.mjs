import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const readme = await readFile(new URL('../README.md', import.meta.url), 'utf8');
const entry = readme.replace(/<details>[\s\S]*?<\/details>/g, '');

test('GitHub 首页以下载和三个用途为主，安装核验与开发说明按需展开', () => {
  assert.match(entry, /\[下载 Windows 安装版\]/);
  assert.match(entry, /无需账号/);
  assert.match(entry, /(?:尚无|未做)商业代码签名/);
  assert.equal((entry.match(/^- /gm) ?? []).length, 3);
  assert.doesNotMatch(entry, /```|\| ---|来源提交|SHA-256|多设备协作/);
  assert.match(readme, /<summary>参与开发<\/summary>/);
  assert.match(readme, /\]\(CONTRIBUTING.md\)/);
  assert.match(readme, /\]\(docs\/UPDATE-RECOVERY.md\)/);
});
