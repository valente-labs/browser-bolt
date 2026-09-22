// Public asset preparation only. Does not deploy or expose the loopback preview server.
import {readFile, writeFile, mkdir, copyFile, mkdtemp, rename, readdir, lstat} from 'node:fs/promises';
import {resolve, dirname, basename, relative} from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
const repository = process.env.BROWSER_BOLT_REPOSITORY;
const tag = process.env.BROWSER_BOLT_RELEASE_TAG;
if (Boolean(repository) !== Boolean(tag)) throw new Error('Repository and release tag must be supplied together');
if (repository && (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) || !/^v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?$/.test(tag))) throw new Error('Invalid public repository or tag');
const packageVersion = (await readFile(resolve(here,'../pyproject.toml'),'utf8')).match(/^version\s*=\s*"([^"]+)"/m)?.[1];
if (tag && tag !== `v${packageVersion}`) throw new Error('Release tag must match the package version');
const publicRelease = repository ? {
  repositoryUrl: `https://github.com/${repository}`,
  tag,
  wheelUrl: `https://github.com/${repository}/releases/download/${tag}/jev_qwerebras_ultrafast-${packageVersion}-py3-none-any.whl`,
  requirementsUrl: `https://github.com/${repository}/releases/download/${tag}/requirements-mcp.txt`,
  checksumsUrl: `https://github.com/${repository}/releases/download/${tag}/SHA256SUMS`,
} : null;
const destination = resolve(process.env.STATIC_OUT || resolve(here, '../dist/browser-bolt-site'));
await mkdir(dirname(destination),{recursive:true});
try {
  const existing = await lstat(destination);
  if (!existing.isDirectory() || existing.isSymbolicLink()) throw new Error('Output must be an owned build directory');
  const marker = JSON.parse(await readFile(resolve(destination,'version.json'),'utf8'));
  if (marker.product !== 'Browser Bolt' || marker.mode !== 'static-preview') throw new Error('Output is not a Browser Bolt build');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  // A directory with no marker must never be silently replaced.
  try { await lstat(destination); throw new Error('Existing output has no ownership marker'); }
  catch (missing) { if (missing.code !== 'ENOENT') throw missing; }
}
const out = await mkdtemp(resolve(dirname(destination),'.browser-bolt-build-'));
const routes = ['', 'start', 'pricing', 'security', 'terms', 'privacy'];
const assets = ['brand.js','styles.css','brand/browser-bolt.png','demo/browser-bolt-demo.mp4','demo/poster.jpg','demo/captions.en.vtt','demo/transcript.html','demo/transcript.md','demo/browser-bolt-demo-mobile.mp4','demo/poster-mobile.png','demo/captions.mobile.en.vtt','demo/transcript-mobile.html','demo/transcript-mobile.md'];
let html = await readFile(resolve(here,'public/index.html'),'utf8');
html = html.replace('<html lang="en">','<html lang="en" data-distribution="static">')
  .replace('  <script defer src="/assets/readiness.js"></script>\n','')
  .replace('<a class="nav-account" href="/dashboard">Account preview</a>','<a class="nav-account" href="/start">Local setup</a>')
  .replace('<a href="/launch">Launch review</a>','');
html = html.replace('Draft terms','BYOK terms').replace('Draft privacy','Privacy')
  .replace('Enable JavaScript to use the local setup and launch review.','Enable JavaScript to use the setup guide.')
  .replace('Inspect measured native results, configure your host, and explore the planned managed account.','Inspect measured native results and configure your own browser-capable host.');
if (publicRelease) html = html.replace('  <script defer src="/assets/app.js"></script>','  <script defer src="/assets/release.js"></script>\n  <script defer src="/assets/app.js"></script>');
for (const route of routes) {
  const folder = resolve(out,route); await mkdir(folder,{recursive:true});
  await writeFile(resolve(folder,'index.html'),html);
}
for (const asset of assets) {
  const dest = resolve(out,'assets',asset); await mkdir(dirname(dest),{recursive:true});
  await copyFile(resolve(here,'public/assets',asset),dest);
}
await copyFile(resolve(here,'public/assets/public-app.js'),resolve(out,'assets/app.js'));
if (publicRelease) await writeFile(resolve(out,'assets/release.js'),`window.BROWSER_BOLT_RELEASE = Object.freeze(${JSON.stringify(publicRelease)});\n`);
await copyFile(resolve(here,'../SECURITY.md'),resolve(out,'SECURITY.md'));
await mkdir(resolve(out,'docs'),{recursive:true});
for (const name of ['MCP.md','HOST.md','LIVE_HOST.md','NATIVE_COMPARISON.md','SPRINTS.md']) await copyFile(resolve(here,'../docs',name),resolve(out,'docs',name));
await mkdir(resolve(out,'docs/benchmarks'),{recursive:true});
for (const name of ['native-sprints-2026-09-18.json','mcp-native-smoke-2026-09-18.json','mcp-jev-choice-2026-09-18.json','installed-host-checkout-2026-09-21.json']) await copyFile(resolve(here,'../docs/benchmarks',name),resolve(out,'docs/benchmarks',name));
await writeFile(resolve(out,'404.html'),'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Page not found | Browser Bolt</title><h1>Page not found</h1><p>This page is not available.</p><a href="/">Return to Browser Bolt</a></html>');
await writeFile(resolve(out,'_headers'),`/*
  Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; connect-src 'none'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer
  X-Frame-Options: DENY
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
/assets/demo/browser-bolt-demo-mobile.mp4
  Content-Type: video/mp4
/assets/demo/poster-mobile.png
  Content-Type: image/png
/assets/demo/captions.mobile.en.vtt
  Content-Type: text/vtt; charset=utf-8
/assets/demo/transcript-mobile.html
  Content-Type: text/html; charset=utf-8
/assets/demo/transcript-mobile.md
  Content-Type: text/markdown; charset=utf-8
/assets/demo/browser-bolt-demo.mp4
  Content-Type: video/mp4
/assets/demo/poster.jpg
  Content-Type: image/jpeg
/assets/demo/captions.en.vtt
  Content-Type: text/vtt; charset=utf-8
/assets/demo/transcript.html
  Content-Type: text/html; charset=utf-8
/assets/demo/transcript.md
  Content-Type: text/markdown; charset=utf-8
`);
await writeFile(resolve(out,'robots.txt'),publicRelease ? 'User-agent: *\nAllow: /\n' : 'User-agent: *\nDisallow: /\n');
await copyFile(resolve(here,'../LICENSE'),resolve(out,'LICENSE.txt'));
await writeFile(resolve(out,'NOTICE.txt'),'Browser Bolt is a local fork of browser-use/jev-ultrafast at upstream commit 452c1ad2dd628008f1d5608f28158d76e49e6cc0. Original MIT license and Browser Use attribution are retained in LICENSE.txt. Website additions are by Valente Labs.\n');
const files = {};
async function inventory(folder) {
  for (const entry of (await readdir(folder,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
    const path = resolve(folder,entry.name);
    if (entry.isDirectory()) await inventory(path);
    else files[relative(out,path).split('\\').join('/')] = createHash('sha256').update(await readFile(path)).digest('hex');
  }
}
await inventory(out);
const releaseId = createHash('sha256').update(JSON.stringify(files)).digest('hex');
await writeFile(resolve(out,'manifest.json'),JSON.stringify({releaseId,files},null,2));
await writeFile(resolve(out,'version.json'),JSON.stringify({product:'Browser Bolt',mode:'static-preview',managedAvailable:false,...(publicRelease?{release:tag}:{releaseApproved:false}),releaseId},null,2));
try {
  await lstat(destination);
  const archive = resolve(dirname(destination),'archive',String(new Date().getUTCFullYear()));
  await mkdir(archive,{recursive:true});
  await rename(destination,resolve(archive,`${basename(destination)}-${Date.now()}`));
  await writeFile(resolve(archive,'ARCHIVED.md'),'Previous local static builds retained for review and rollback. Never deploy this directory.\n');
} catch(error) { if(error.code !== 'ENOENT') throw error; }
await rename(out,destination);
console.log(`Prepared static review artifact at ${destination}. Release ${releaseId}. No deployment performed. Indexing ${publicRelease ? 'configured for the reviewed public release' : 'disabled'}.`);
