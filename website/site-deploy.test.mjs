import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { handleRequest, release, releaseAssets, releases } from './worker.mjs';

test('the website deploy is manual, restricted to the reviewed main, and scopes credentials to Wrangler', async () => {
  const workflow = (await readFile(new URL('../.github/workflows/website-deploy.yml', import.meta.url), 'utf8'))
    .replaceAll('\r\n', '\n');
  const triggers = workflow.split('\non:\n')[1].split('\npermissions:', 1)[0];
  assert.deepEqual([...triggers.matchAll(/^  (\w+):/gm)].map(match => match[1]), ['workflow_dispatch']);
  assert.match(triggers, /verify_only:\n(?:[^\n]+\n)*?        default: false\n        type: boolean/);
  assert.match(workflow, /permissions:\n  contents: read\n/);
  assert.doesNotMatch(workflow, /:\s*write\b/);
  assert.match(workflow, /group: workbox-website\n  cancel-in-progress: false/);
  for (const gate of [
    "github.repository == '232577/workbox'",
    "github.ref == 'refs/heads/main'",
    "github.actor == '232577'",
    "github.triggering_actor == '232577'",
    'github.sha == inputs.source_sha',
  ]) {
    assert.ok(workflow.includes(gate), gate);
  }
  assert.match(workflow, /environment: workbox-website/);
  assert.match(workflow, /persist-credentials: false/);
  const [checks, deploy] = workflow.split('      - name: Deploy the existing WorkBox Worker\n');
  const [deployment, onlineCheck] = deploy.split('      - name: Verify the public website\n');
  assert.match(checks, /node --test website\/\*\.test\.mjs/);
  assert.match(checks, /wrangler@4\.127\.1\b/);
  assert.match(checks, /- name: Install pinned Wrangler\n        if: \$\{\{ !inputs\.verify_only \}\}/);
  assert.match(checks, /node scripts\/verify_website_deploy\.mjs preflight/);
  assert.doesNotMatch(checks.split('- name: Verify current main and the public release')[1], /if:/);
  assert.doesNotMatch(checks + onlineCheck, /CLOUDFLARE_(API_TOKEN|ACCOUNT_ID)|\$\{\{ secrets\./);
  assert.match(deployment, /^        if: \$\{\{ !inputs\.verify_only \}\}\n/);
  assert.match(deployment, /CLOUDFLARE_API_TOKEN: \$\{\{ secrets\.CLOUDFLARE_API_TOKEN \}\}/);
  assert.match(deployment, /CLOUDFLARE_ACCOUNT_ID: \$\{\{ vars\.CLOUDFLARE_ACCOUNT_ID \}\}/);
  assert.match(deployment, /export WRANGLER_OUTPUT_FILE_PATH/);
  assert.match(deployment, /"\$wrangler" versions upload --config website\/wrangler\.jsonc/);
  assert.match(deployment, /version_id=\$\(node scripts\/verify_website_deploy\.mjs version-upload "\$WRANGLER_OUTPUT_FILE_PATH"\)/);
  assert.match(deployment, /"\$wrangler" versions deploy "\$version_id@100%" --yes --config website\/wrangler\.jsonc/);
  assert.match(deployment, /--message "WorkBox site \$GITHUB_SHA"/);
  assert.match(deployment, /deployment_id=\$\(node scripts\/verify_website_deploy\.mjs version-deploy "\$WRANGLER_OUTPUT_FILE_PATH"\)/);
  assert.match(deployment, /printf [^\n]+"\$GITHUB_SHA" "\$version_id" "\$deployment_id"/);
  assert.doesNotMatch(deployment, /" (?:deploy\b|triggers\b)/);
  assert.match(onlineCheck, /node scripts\/verify_website_deploy\.mjs online/);
  assert.doesNotMatch(onlineCheck, /if:/);
});

test('Wrangler deployment evidence requires a unique result for the existing Worker and a valid ID', async () => {
  const { wranglerResultId } = await import('../scripts/verify_website_deploy.mjs');
  const session = { type: 'wrangler-session' };
  const upload = { type: 'version-upload', version: 1, worker_name: 'workbox-downloads',
    version_id: '12345678-1234-4234-8234-123456789012' };
  const deploy = { type: 'version-deploy', version: 1, worker_name: 'workbox-downloads',
    deployment_id: '12345678-1234-4234-8234-123456789013' };
  const jsonl = (...records) => records.map(record => JSON.stringify(record)).join('\n') + '\n';

  assert.equal(wranglerResultId(jsonl(session, upload), upload.type), upload.version_id);
  assert.equal(wranglerResultId(jsonl(session, upload, session, deploy), deploy.type), deploy.deployment_id);
  assert.throws(() => wranglerResultId(jsonl(session), upload.type), /one .* result/);
  assert.throws(() => wranglerResultId(jsonl(upload, upload), upload.type), /one .* result/);
  assert.throws(() => wranglerResultId(jsonl({ ...upload, worker_name: 'another-worker' }), upload.type), /Worker/);
  assert.throws(() => wranglerResultId(jsonl({ ...upload, version: 2 }), upload.type), /schema/);
  assert.throws(() => wranglerResultId(jsonl({ ...upload, version_id: 'not-an-id' }), upload.type), /ID/);
  assert.throws(() => wranglerResultId(jsonl(upload, { ...deploy, deployment_id: null }), deploy.type), /ID/);
});

test('release verification rejects a stale main or any changed public asset identity', async () => {
  const { verifyPublicRelease } = await import('../scripts/verify_website_deploy.mjs');
  const localManifest = await (await handleRequest(new Request(
    'https://workbox.ltxiangzi.dpdns.org/updates/stable.json',
  ), {})).json();
  const expectedSource = 'a'.repeat(40);
  const main = { ref: 'refs/heads/main', object: { type: 'commit', sha: expectedSource } };
  const published = {
    ...localManifest,
    assets: [...localManifest.assets, { name: 'BUILD_PROVENANCE.json' }],
  };
  assert.doesNotThrow(() => verifyPublicRelease(expectedSource, main, release, localManifest, published));
  assert.throws(() => verifyPublicRelease(expectedSource, {
    ...main, object: { ...main.object, sha: 'b'.repeat(40) },
  }, release, localManifest, published), /main/);
  assert.throws(() => verifyPublicRelease(expectedSource, main, {
    ...release, version: '2.0.0',
  }, localManifest, published), /version/);
  for (const changed of [
    { tag_name: 'v1.0.0.20261123.1' },
    { draft: true },
    { prerelease: true },
    { html_url: 'https://example.com/release' },
  ]) {
    assert.throws(() => verifyPublicRelease(expectedSource, main, release, localManifest, {
      ...published, ...changed,
    }), /latest public release/);
  }
  for (const changed of [
    { digest: `sha256:${'0'.repeat(64)}` },
    { size: release.size + 1 },
    { browser_download_url: 'https://example.com/installer.exe' },
    { state: 'processing' },
    { name: 'other.exe' },
  ]) {
    assert.throws(() => verifyPublicRelease(expectedSource, main, release, localManifest, {
      ...published, assets: [{ ...published.assets[0], ...changed }, ...published.assets.slice(1)],
    }), /asset metadata/);
  }
  assert.throws(() => verifyPublicRelease(expectedSource, main, release, localManifest, {
    ...published, assets: [...published.assets, published.assets[0]],
  }), /asset metadata/);
});

test('online byte checks wait only for known release propagation', async () => {
  const { waitForLiveRelease } = await import('../scripts/verify_website_deploy.mjs');
  const previous = releases[1];
  const asManifest = item => ({ tag_name: item.tag, draft: false, prerelease: false,
    html_url: `https://github.com/232577/workbox/releases/tag/${item.tag}`, assets: releaseAssets(item) });
  const currentManifest = asManifest(release);
  const olderManifest = asManifest(previous);
  const opts = { timeoutMs: 1_000, intervalMs: 1 };
  function mockVersions(pairs) {
    let calls = 0;
    const fetcher = async address => {
      const [health, stable] = pairs[Math.min(Math.floor(calls++ / 2), pairs.length - 1)];
      const path = new URL(address).pathname;
      if (path === '/healthz') return Response.json(health);
      assert.equal(path, '/updates/stable.json');
      return Response.json(stable, { headers: { 'Cache-Control': 'no-store' } });
    };
    return { fetcher, calls: () => calls };
  }
  const converges = mockVersions([[previous, olderManifest], [release, olderManifest], [release, currentManifest]]);
  assert.equal(await waitForLiveRelease(release, currentManifest, releases.slice(1), converges.fetcher, opts), 3);
  assert.equal(converges.calls(), 6);

  const corrupt = mockVersions([[release, { ...currentManifest, assets: [] }]]);
  await assert.rejects(waitForLiveRelease(release, currentManifest, releases.slice(1), corrupt.fetcher, opts), /stable manifest/);
  assert.equal(corrupt.calls(), 2);
  const unknown = mockVersions([[{ ...previous, tag: 'v1.0.0.20261201.1' }, olderManifest]]);
  await assert.rejects(waitForLiveRelease(release, currentManifest, releases.slice(1), unknown.fetcher, opts), /unexpected.*health/i);
  assert.equal(unknown.calls(), 2);
  const stale = mockVersions([[previous, olderManifest]]);
  await assert.rejects(waitForLiveRelease(release, currentManifest, releases.slice(1), stale.fetcher,
    { timeoutMs: 30, intervalMs: 5 }), /did not converge/);
});

test('online verification checks anonymous bytes, range, page links, history, stats and video', async t => {
  const { verifyOnlineSite } = await import('../scripts/verify_website_deploy.mjs');
  const installer = Buffer.alloc(2048, 65);
  const video = Buffer.alloc(2050, 66);
  const hash = bytes => createHash('sha256').update(bytes).digest('hex');
  const primary = {
    version: '1.0.0', tag: 'v1.0.0.20261124.2', edition: '正式安装版', date: '2026-11-24',
    filename: 'WorkBox-1.0.0-Setup-x64-20261124-r2.exe', size: installer.length, sha256: hash(installer),
  };
  const previous = { filename: 'WorkBox-1.0.0-Setup-x64-20261123-r1.exe', size: 100 };
  const media = { path: '/media/test.mp4', size: video.length, sha256: hash(video) };
  const asManifest = item => ({ tag_name: item.tag, assets: releaseAssets(item) });
  const manifest = asManifest(primary);
  const downloadPath = `/download/${primary.filename}`;
  const previousPath = `/download/${previous.filename}`;
  const fallback = `https://github.com/232577/workbox/releases/download/${primary.tag}/${primary.filename}`;
  const downloadId = '?download=12345678-1234-4234-8234-123456789012';

  function mockFetch(changes = {}) {
    return async (address, options) => {
      const url = new URL(address);
      changes.paths?.push(url.pathname);
      assert.equal(url.origin, 'https://workbox.ltxiangzi.dpdns.org');
      assert.equal(new Headers(options.headers).has('Cookie'), false);
      const range = new Headers(options.headers).get('Range');
      switch (url.pathname) {
        case '/healthz': return Response.json(changes.primary ?? primary);
        case '/updates/stable.json': return Response.json(changes.manifest ?? manifest, { headers: { 'Cache-Control': 'no-store' } });
        case '/': return new Response(`<a id="download-title" href="${changes.homeLink ?? downloadPath}"></a><a href="${fallback}"></a>`);
        case '/guide.html': return new Response(`<a href="${fallback}"></a>`);
        case '/releases.html': return new Response(`<section id="current-release"><a href="${downloadPath}"></a><a href="${fallback}"></a>${primary.tag} ${primary.sha256}</section><a href="${previousPath}"></a>`);
        case '/api/admin/stats': return new Response(null, { status: changes.statsStatus ?? 401 });
        case previousPath:
          assert.equal(options.method, 'HEAD');
          return new Response(null, { status: changes.historyStatus ?? 200, headers: { 'Content-Length': String(previous.size) } });
        case downloadPath:
          if (!url.search) {
            assert.equal(options.redirect, 'manual');
            return new Response(null, { status: 307, headers: { Location: changes.location ?? downloadPath + downloadId } });
          }
          assert.equal(url.search, downloadId);
          if (range) {
            assert.equal(range, 'bytes=0-1023');
            return new Response(changes.installerRange ?? installer.subarray(0, 1024), {
              status: 206, headers: { 'Content-Range': changes.contentRange ?? `bytes 0-1023/${primary.size}` },
            });
          }
          return new Response(changes.installerFull ?? installer);
        case media.path:
          if (range) {
            assert.equal(range, 'bytes=0-1023');
            return new Response(video.subarray(0, 1024), {
              status: 206, headers: { 'Content-Range': `bytes 0-1023/${media.size}` },
            });
          }
          return new Response(video);
        default: throw new Error(`Unexpected URL ${url}`);
      }
    };
  }

  const paths = [];
  await verifyOnlineSite(primary, manifest, previous, media, mockFetch({ paths }));
  assert.deepEqual(paths.slice(0, 2), ['/healthz', '/updates/stable.json']);
  const brokenHistoryPaths = [];
  await assert.rejects(verifyOnlineSite(primary, manifest, previous, media,
    mockFetch({ historyStatus: 404, paths: brokenHistoryPaths })), /HTTP 404/);
  assert.equal(brokenHistoryPaths.filter(path => path === previousPath).length, 1);
  for (const [changes, expectedError] of [
    [{ installerFull: Buffer.alloc(installer.length + 1) }, /exceeds/],
    [{ installerFull: Buffer.alloc(installer.length) }, /SHA-256/],
    [{ installerRange: Buffer.alloc(1024) }, /Range bytes/],
    [{ contentRange: 'bytes 0-1023/9999' }, /Content-Range/],
    [{ location: 'https://example.com/installer' }, /download redirect/],
    [{ homeLink: previousPath }, /home page/],
    [{ statsStatus: 200 }, /401/],
  ]) {
    await assert.rejects(verifyOnlineSite(primary, manifest, previous, media, mockFetch(changes)), expectedError);
  }

  const packages = [
    ['20261123', Buffer.alloc(1030, 67)],
    ['20261124', Buffer.alloc(512, 68)],
  ].map(([date, bytes]) => {
    const name = `WorkBox-1.0.0-Delta-x64-${date}-r1-to-20261124-r2.wbdelta`;
    return {
      bytes,
      descriptor: {
        name, from_tag: `v1.0.0.${date}.1`, to_tag: primary.tag,
        size: bytes.length, sha256: hash(bytes), state: 'uploaded',
        url: `https://github.com/232577/workbox/releases/download/${primary.tag}/${name}`,
      },
      redirect: `https://release-assets.githubusercontent.com/${name}?key=fixture`,
    };
  });
  const current = { ...primary, deltas: packages.map(item => item.descriptor) };
  const currentManifest = asManifest(current);

  function mockDeltas(changes = {}) {
    const fetchSite = mockFetch({ primary: current, manifest: changes.manifest ?? currentManifest });
    return async (address, options) => {
      if (new URL(address).origin === 'https://workbox.ltxiangzi.dpdns.org') return fetchSite(address, options);
      const item = packages.find(entry => [entry.descriptor.url, entry.redirect].includes(address));
      assert.ok(item, 'Only the expected GitHub assets may be requested');
      assert.equal(options.redirect, 'manual');
      const headers = new Headers(options.headers);
      const range = headers.get('Range');
      assert.equal(headers.has('Authorization') || headers.has('Cookie'), false);
      changes.requests?.push([address, range]);
      if (address === item.descriptor.url)
        return new Response(null, { status: 302, headers: { Location: changes.redirect ?? item.redirect } });
      if (!range) return new Response(changes.full?.(item.bytes) ?? item.bytes);
      const count = Math.min(1024, item.bytes.length);
      assert.equal(range, `bytes=0-${count - 1}`);
      return new Response(changes.range?.(count) ?? item.bytes.subarray(0, count), {
        status: 206, headers: { 'Content-Range': changes.contentRange ?? `bytes 0-${count - 1}/${item.bytes.length}` },
      });
    };
  }

  await t.test('reads the full and ranged contents of both advertised GitHub deltas', async () => {
    const requests = [];
    await verifyOnlineSite(current, currentManifest, previous, media, mockDeltas({ requests }));
    assert.equal(requests.length, 8);
    for (const { bytes, descriptor, redirect } of packages) {
      for (const url of [descriptor.url, redirect]) {
        assert.deepEqual(requests.filter(([address]) => address === url).map(([, range]) => range),
          [null, `bytes=0-${Math.min(1024, bytes.length) - 1}`]);
      }
    }
  });

  await t.test('rejects wrong delta bytes, ranges or destinations', async () => {
    for (const [changes, error] of [
      [{ full: () => Buffer.alloc(1) }, /Download size/],
      [{ full: bytes => Buffer.alloc(bytes.length) }, /SHA-256/],
      [{ contentRange: 'bytes 0-1023/9999' }, /Content-Range/],
      [{ range: count => Buffer.alloc(count) }, /Range bytes/],
      [{ redirect: 'https://example.com/foreign.wbdelta' }, /Untrusted GitHub asset URL/],
    ]) {
      await assert.rejects(verifyOnlineSite(current, currentManifest, previous, media, mockDeltas(changes)), error);
    }
    const changedManifest = { ...currentManifest, assets: currentManifest.assets.slice(0, -1) };
    await assert.rejects(verifyOnlineSite(current, changedManifest, previous, media,
      mockDeltas({ manifest: changedManifest })), /Invalid local asset metadata/);
  });
});
