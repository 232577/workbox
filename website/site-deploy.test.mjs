import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { handleRequest, release } from './worker.mjs';

test('the website deploy is manual, restricted to the reviewed main, and scopes credentials to Wrangler', async () => {
  const workflow = (await readFile(new URL('../.github/workflows/website-deploy.yml', import.meta.url), 'utf8'))
    .replaceAll('\r\n', '\n');
  const triggers = workflow.split('\non:\n')[1].split('\npermissions:', 1)[0];
  assert.deepEqual([...triggers.matchAll(/^  (\w+):/gm)].map(match => match[1]), ['workflow_dispatch']);
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
  assert.match(checks, /node scripts\/verify_website_deploy\.mjs preflight/);
  assert.doesNotMatch(checks + onlineCheck, /CLOUDFLARE_(API_TOKEN|ACCOUNT_ID)|\$\{\{ secrets\./);
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
    { tag_name: 'v1.0.0.20261120.1' },
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

test('online verification checks anonymous bytes, range, page links, history, stats and video', async () => {
  const { verifyOnlineSite } = await import('../scripts/verify_website_deploy.mjs');
  const installer = Buffer.alloc(2048, 65);
  const video = Buffer.alloc(2050, 66);
  const hash = bytes => createHash('sha256').update(bytes).digest('hex');
  const primary = { ...release, size: installer.length, sha256: hash(installer) };
  const previous = { filename: 'WorkBox-1.0.0-Setup-x64-20261120-r1.exe', size: 100 };
  const media = { path: '/media/test.mp4', size: video.length, sha256: hash(video) };
  const manifest = { tag_name: primary.tag };
  const downloadPath = `/download/${primary.filename}`;
  const previousPath = `/download/${previous.filename}`;
  const fallback = `https://github.com/232577/workbox/releases/download/${primary.tag}/${primary.filename}`;
  const downloadId = '?download=12345678-1234-4234-8234-123456789012';

  function mockFetch(changes = {}) {
    return async (address, options) => {
      const url = new URL(address);
      assert.equal(url.origin, 'https://workbox.ltxiangzi.dpdns.org');
      assert.equal(new Headers(options.headers).has('Cookie'), false);
      const range = new Headers(options.headers).get('Range');
      switch (url.pathname) {
        case '/healthz': return Response.json(primary);
        case '/updates/stable.json': return Response.json(manifest, { headers: { 'Cache-Control': 'no-store' } });
        case '/': return new Response(`<a id="download-title" href="${changes.homeLink ?? downloadPath}"></a><a href="${fallback}"></a>`);
        case '/guide.html': return new Response(`<a href="${fallback}"></a>`);
        case '/releases.html': return new Response(`<section id="current-release"><a href="${downloadPath}"></a><a href="${fallback}"></a>${primary.tag} ${primary.sha256}</section><a href="${previousPath}"></a>`);
        case '/api/admin/stats': return new Response(null, { status: changes.statsStatus ?? 401 });
        case previousPath:
          assert.equal(options.method, 'HEAD');
          return new Response(null, { headers: { 'Content-Length': String(previous.size) } });
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

  await verifyOnlineSite(primary, manifest, previous, media, mockFetch());
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
});
