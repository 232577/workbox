import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { uuid } from '../website/admin.mjs';
import { handleRequest, release, releaseAssets, releases } from '../website/worker.mjs';

const repository = '232577/workbox';
const githubApi = `https://api.github.com/repos/${repository}`;
const site = 'https://workbox.ltxiangzi.dpdns.org';
const workerName = 'workbox-downloads';
const stablePath = '/updates/stable.json';
const assetFields = ['name', 'size', 'state', 'digest', 'browser_download_url'];
const rangeBytes = 1024;
const githubAssetOrigins = new Set([
  'https://github.com', 'https://release-assets.githubusercontent.com', 'https://objects.githubusercontent.com',
]);
const redirectStatuses = new Set([301, 302, 303, 307, 308]);

export function wranglerResultId(output, type) {
  assert.ok(type === 'version-upload' || type === 'version-deploy', 'Unknown Wrangler result type');
  const results = output.split(/\r?\n/).filter(line => line.trim())
    .map(line => JSON.parse(line)).filter(row => row?.type === type);
  assert.equal(results.length, 1, `Expected one ${type} result`);
  const [result] = results;
  assert.equal(result.version, 1, 'Unexpected Wrangler result schema');
  assert.equal(result.worker_name, workerName, 'Wrangler result is for an unexpected Worker');
  const id = type === 'version-upload' ? result.version_id : result.deployment_id;
  assert.ok(typeof id === 'string' && uuid.test(id), `Invalid Wrangler ${type} ID`);
  return id;
}

export function verifyPublicRelease(expectedSource, main, siteRelease, localManifest, published) {
  assert.ok(/^[a-f0-9]{40}$/.test(expectedSource), 'Expected a full main SHA');
  assert.equal(main.ref, 'refs/heads/main', 'GitHub ref is not main');
  assert.equal(main.object?.type, 'commit', 'GitHub main does not identify a commit');
  assert.equal(main.object.sha, expectedSource, 'GitHub main changed after dispatch');
  assert.equal(siteRelease.version, '1.0.0', 'Unexpected WorkBox version');
  assert.match(siteRelease.tag, /^v1\.0\.0\.20[0-9]{6}(?:\.[1-9][0-9]{0,3})?$/, 'Unexpected WorkBox release tag');
  assert.ok(Number.isSafeInteger(siteRelease.size) && siteRelease.size > 0, 'Invalid installer size');
  assert.match(siteRelease.sha256, /^[a-f0-9]{64}$/, 'Invalid installer SHA-256');

  const metadata = {
    tag_name: siteRelease.tag,
    draft: false,
    prerelease: false,
    html_url: `https://github.com/${repository}/releases/tag/${siteRelease.tag}`,
  };
  const fields = Object.keys(metadata);
  assert.ok(fields.every(field => localManifest[field] === metadata[field]), 'Invalid local stable manifest');
  assert.ok(fields.every(field => published[field] === metadata[field]), 'GitHub latest public release differs from the local manifest');
  assert.deepEqual(localManifest.assets, releaseAssets(siteRelease), 'Invalid local asset metadata');
  assert.ok(Array.isArray(published.assets), 'GitHub latest public release has no asset metadata');
  for (const asset of localManifest.assets) {
    const matches = published.assets.filter(candidate => candidate.name === asset.name);
    assert.ok(matches.length === 1 && assetFields.every(field => matches[0][field] === asset[field]),
      `GitHub asset metadata differs for ${asset.name}`);
  }
}

async function request(url, { status = 200, timeout = 15_000, ...options } = {}, fetcher = fetch) {
  const response = await fetcher(url, {
    cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(timeout), ...options,
  });
  assert.ok([status].flat().includes(response.status), `${url} returned HTTP ${response.status}; expected ${status}`);
  return response;
}

async function readBytes(response, size) {
  assert.ok(response.body, 'Download has no body');
  const hash = createHash('sha256');
  const prefix = Buffer.alloc(Math.min(rangeBytes, size));
  let length = 0;
  for await (const chunk of response.body) {
    assert.ok(length + chunk.length <= size, `Download exceeds ${size} catalog bytes`);
    if (length < prefix.length) prefix.set(chunk.subarray(0, prefix.length - length), length);
    hash.update(chunk);
    length += chunk.length;
  }
  assert.equal(length, size, 'Download size differs from the catalog');
  return { sha256: hash.digest('hex'), prefix };
}

async function verifyFileBytes(full, url, { size, sha256 }, fetcher) {
  const downloaded = await readBytes(full, size);
  assert.equal(downloaded.sha256, sha256, `${url} SHA-256 differs from the catalog`);
  const count = Math.min(rangeBytes, size);
  const partial = await request(url, { status: 206, headers: { Range: `bytes=0-${count - 1}` } }, fetcher);
  assert.equal(partial.headers.get('content-range'), `bytes 0-${count - 1}/${size}`, `${url} Content-Range`);
  const range = await readBytes(partial, count);
  assert.deepEqual(range.prefix, downloaded.prefix, `${url} Range bytes differ from the complete download`);
}

async function verifyFile(path, asset, fetcher, installer = false) {
  let url = site + path;
  let full = await request(url, installer
    ? { status: [200, 307], redirect: 'manual', timeout: 120_000 }
    : { timeout: 30_000 }, fetcher);
  if (full.status === 307) {
    const location = full.headers.get('location');
    await full.body?.cancel();
    assert.ok(location, 'Missing download redirect location');
    const target = new URL(location, url);
    assert.ok(target.origin === site && target.pathname === path && !target.hash
      && !target.username && !target.password && target.searchParams.size === 1
      && uuid.test(target.searchParams.get('download') ?? ''), 'Unexpected download redirect');
    url = target.href;
    full = await request(url, { timeout: 120_000 }, fetcher);
  }
  // Keep the WorkBox download ID so a repeat range does not add a download start.
  await verifyFileBytes(full, url, asset, fetcher);
}

async function verifyDeltaFile({ browser_download_url: url, size, digest }, fetcher) {
  const fetchAsset = async (address, options) => {
    let target = new URL(address);
    const headers = new Headers(options.headers);
    headers.set('Accept', 'application/octet-stream');
    for (let redirects = 0; redirects <= 5; redirects++) {
      assert.ok(githubAssetOrigins.has(target.origin) && !target.username && !target.password && !target.hash,
        'Untrusted GitHub asset URL');
      const response = await fetcher(target.href, { ...options, headers, redirect: 'manual' });
      if (!redirectStatuses.has(response.status)) return response;
      const location = response.headers.get('location');
      await response.body?.cancel();
      assert.ok(location, 'Missing GitHub asset redirect location');
      target = new URL(location, target);
    }
    throw new Error('Too many GitHub asset redirects');
  };
  const full = await request(url, { timeout: 120_000 }, fetchAsset);
  await verifyFileBytes(full, url, { size, sha256: digest.slice('sha256:'.length) }, fetchAsset);
}

export async function waitForLiveRelease(primary, manifest, history, fetcher = fetch,
  { timeoutMs = 60_000, intervalMs = 5_000 } = {}) {
  const deadline = performance.now() + timeoutMs;
  let attempts = 0;
  let observed = '';
  while (performance.now() < deadline) {
    const options = { timeout: Math.min(15_000, Math.ceil(deadline - performance.now())) };
    const [health, { feed, cacheControl }] = await Promise.all([
      request(`${site}/healthz`, options, fetcher).then(response => response.json()),
      request(site + stablePath, options, fetcher).then(async response => ({
        feed: await response.json(), cacheControl: response.headers.get('cache-control'),
      })),
    ]);
    attempts++;
    observed = `health ${health?.tag}, stable ${feed?.tag_name}`;
    assert.equal(cacheControl, 'no-store');
    const currentHealth = health?.tag === primary.tag;
    const currentFeed = feed?.tag_name === primary.tag;
    if (currentHealth) {
      assert.deepEqual(health, primary, 'Public Worker release differs from the deployed source');
    } else {
      const prior = history.find(item => item.tag === health?.tag);
      assert.ok(prior, `Unexpected public health release ${health?.tag}`);
      assert.deepEqual(health, prior, 'Public health response differs from its historical release');
    }
    if (currentFeed) {
      assert.deepEqual(feed, manifest, 'Public stable manifest differs from the deployed source');
    } else {
      const prior = history.find(item => item.tag === feed?.tag_name);
      assert.ok(prior && feed.draft === false && feed.prerelease === false
        && feed.html_url === `https://github.com/${repository}/releases/tag/${prior.tag}`,
      `Unexpected public stable release ${feed?.tag_name}`);
      assert.deepEqual(feed.assets, releaseAssets(prior), 'Public stable manifest differs from its historical release');
    }
    if (currentHealth && currentFeed) return attempts;
    await delay(Math.min(intervalMs, Math.max(0, deadline - performance.now())));
  }
  throw new Error(`Public release ${primary.tag} did not converge after ${attempts} probes (${observed})`);
}

export async function verifyOnlineSite(primary, manifest, previous, media, fetcher = fetch) {
  const assets = releaseAssets(primary);
  assert.deepEqual(manifest.assets, assets, 'Invalid local asset metadata');
  const attempts = await waitForLiveRelease(primary, manifest, releases.slice(1), fetcher);
  const get = (path, options) => request(site + path, options, fetcher);
  const download = `/download/${primary.filename}`;
  const history = `/download/${previous.filename}`;
  const fallback = `https://github.com/${repository}/releases/download/${primary.tag}/${primary.filename}`;
  const [home, guide, releasePage, previousHead, stats] = await Promise.all([
    get('/').then(response => response.text()),
    get('/guide.html').then(response => response.text()),
    get('/releases.html').then(response => response.text()),
    get(history, { method: 'HEAD' }),
    get('/api/admin/stats', { status: 401 }),
  ]);
  await stats.body?.cancel();

  const linksTo = (html, path) => html.includes(`href="${path}"`);
  const homeDownload = home.match(/<a\b[^>]*\bid="download-title"[^>]*>/)?.[0] ?? '';
  const currentRelease = releasePage.match(/<section id="current-release"[\s\S]*?<\/section>/)?.[0] ?? '';
  assert.ok(linksTo(homeDownload, download) && linksTo(home, fallback), 'The home page download differs from the stable release');
  assert.ok(linksTo(guide, fallback), 'The guide download differs from the stable release');
  assert.ok(linksTo(currentRelease, download) && linksTo(currentRelease, fallback)
    && currentRelease.includes(primary.tag) && currentRelease.includes(primary.sha256), 'The release page differs from the stable release');
  assert.ok(linksTo(releasePage, history), 'The release page is missing the previous installer');
  assert.equal(previousHead.headers.get('content-length'), String(previous.size), 'Previous installer size changed');

  await Promise.all([
    verifyFile(download, primary, fetcher, true),
    verifyFile(media.path, media, fetcher),
    ...assets.slice(1).map(asset => verifyDeltaFile(asset, fetcher)),
  ]);
  return attempts;
}

async function main(mode, resultPath) {
  if (mode === 'version-upload' || mode === 'version-deploy') {
    console.log(wranglerResultId(await readFile(resultPath, 'utf8'), mode));
    return;
  }
  const local = await handleRequest(new Request(site + stablePath), {});
  assert.equal(local.status, 200);
  const manifest = await local.json();

  if (mode === 'preflight') {
    const headers = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };
    const [head, published] = await Promise.all([
      request(`${githubApi}/git/ref/heads/main`, { headers }).then(response => response.json()),
      request(`${githubApi}/releases/latest`, { headers }).then(response => response.json()),
    ]);
    verifyPublicRelease(process.env.GITHUB_SHA, head, release, manifest, published);
    console.log(`Verified main ${head.object.sha} and public release ${release.tag}`);
  } else if (mode === 'online') {
    const media = JSON.parse(await readFile(new URL('../website/MEDIA.json', import.meta.url), 'utf8'));
    assert.match(media.active_video, /^public\/media\/[\w-]+\.mp4$/);
    const activeVideo = { ...media.files[media.active_video], path: media.active_video.slice('public'.length) };
    const attempts = await verifyOnlineSite(release, manifest, releases[1], activeVideo);
    console.log(`Verified ${release.tag} after ${attempts} public version probes, with public links, installer, ${manifest.assets.length - 1} GitHub deltas and video digests and ranges, history and anonymous stats denial`);
  } else {
    throw new Error('Use preflight, online, version-upload or version-deploy');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main(process.argv[2], process.argv[3]);
}
