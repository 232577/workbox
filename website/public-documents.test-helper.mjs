import { readFile } from 'node:fs/promises';

export const pagePaths = ['/index.html', '/guide.html', '/releases.html'];
export const documents = Object.fromEntries(await Promise.all(pagePaths.map(async path => [path,
  await readFile(new URL(`./public${path}`, import.meta.url), 'utf8').catch(error => {
    if (error.code === 'ENOENT') return '';
    throw error;
  }),
])));
export const siteHtml = pagePaths.map(path => documents[path]).join('\n');
export function parseTags(html) {
  return [...html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)].map(([, name, raw]) => ({
    name: name.toLowerCase(),
    attrs: Object.fromEntries([...raw.matchAll(/([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)]
      .map(([, key, double, single, bare]) => [key.toLowerCase(), double ?? single ?? bare ?? ''])),
  }));
}
