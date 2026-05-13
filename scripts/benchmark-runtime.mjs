#!/usr/bin/env node
import { chromium } from '@playwright/test';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import process from 'node:process';

const DEFAULT_FIRST_WORLD_COUNT = 500;
const DEFAULT_VISIBLE_COUNT = 8000;
const DEFAULT_TIMEOUT_MS = 180_000;

const args = parseArgs(process.argv.slice(2).filter((arg) => arg !== '--'));
if (args.help) {
  printHelp();
  process.exit(0);
}

const dataDir = requiredPath(args['data-dir'], '--data-dir is required');
const sourceDir = optionalPath(args.source);
const firstWorldCount = intArg(args['first-world-count'], DEFAULT_FIRST_WORLD_COUNT);
const visibleCount = intArg(args['visible-count'], DEFAULT_VISIBLE_COUNT);
const timeoutMs = intArg(args.timeout, DEFAULT_TIMEOUT_MS);
const headed = boolArg(args.headed);
const noBuild = boolArg(args['no-build']);
const clearData = boolArg(args.clear);
const coldAtlas = boolArg(args['cold-atlas']);
const output = typeof args.output === 'string' ? resolve(args.output) : null;

if (!noBuild) runChecked('pnpm', ['build']);
if (coldAtlas) await rm(resolve(dataDir, 'assets', 'atlas'), { recursive: true, force: true });

const server = await startServer(dataDir);
const result = {
  dataDir,
  sourceDir,
  firstWorldCount,
  visibleCount,
  serverUrl: server.url,
  importBackend: {},
  playviewLoading: {},
};

try {
  if (clearData) await postJson(new URL('api/data/clear', server.url), {});
  if (sourceDir) result.importBackend = await measureImport(server.url, sourceDir, firstWorldCount, timeoutMs);
  result.importBackend.atlasBuild = await measureAtlas(server.url);
  result.playviewLoading = await measurePlayview(server.url, visibleCount, timeoutMs, headed);
  printResult(result);
  if (output) {
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
} finally {
  server.stop();
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const raw = argv[index];
    if (!raw.startsWith('--')) throw new Error(`unexpected argument: ${raw}`);
    const key = raw.slice(2);
    if (key === 'help' || key === 'headed' || key === 'no-build' || key === 'clear' || key === 'cold-atlas') {
      parsed[key] = true;
      continue;
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith('--')) throw new Error(`missing value for --${key}`);
    parsed[key] = value;
    index += 1;
  }
  return parsed;
}

function printHelp() {
  console.log(`Image Garden runtime benchmark\n\nUsage:\n  pnpm bench:runtime -- --data-dir DIR [--source PHOTOS] [options]\n\nOptions:\n  --source DIR                 Import this folder before playview benchmark.\n  --clear                      Clear app data before import.\n  --cold-atlas                 Delete atlas cache before measuring atlas build.\n  --first-world-count N        First usable world threshold. Default ${DEFAULT_FIRST_WORLD_COUNT}.\n  --visible-count N            Visible thumbnail threshold. Default ${DEFAULT_VISIBLE_COUNT}.\n  --timeout MS                 Wait timeout. Default ${DEFAULT_TIMEOUT_MS}.\n  --headed                     Run browser headed.\n  --no-build                   Skip pnpm build before benchmarking.\n  --output FILE                Write JSON result.\n`);
}

function requiredPath(value, message) {
  if (typeof value !== 'string' || value.length === 0) throw new Error(message);
  return resolve(value);
}

function optionalPath(value) {
  return typeof value === 'string' && value.length > 0 ? resolve(value) : null;
}

function intArg(value, fallback) {
  if (typeof value !== 'string') return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 1) throw new Error(`invalid integer: ${value}`);
  return parsed;
}

function boolArg(value) {
  return value === true;
}

function runChecked(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', env: cleanEnv() });
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed`);
}

function cleanEnv() {
  const env = { ...process.env };
  delete env.VIRTUAL_ENV;
  return env;
}

async function startServer(dataDir) {
  const child = spawn(
    'uv',
    [
      '--project',
      'studio',
      'run',
      '--extra',
      'onnx',
      'constellation-app',
      '--data-dir',
      dataDir,
      '--host',
      '127.0.0.1',
      '--port',
      '0',
      '--no-open',
    ],
    { env: cleanEnv(), stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let output = '';
  child.stdout.on('data', (chunk) => { output += chunk.toString(); });
  child.stderr.on('data', (chunk) => { output += chunk.toString(); });
  const url = await waitFor(() => {
    if (child.exitCode !== null) {
      throw new Error(`server exited before URL was printed\n${output}`);
    }
    const match = output.match(/http:\/\/127\.0\.0\.1:\d+\//);
    return match?.[0] ?? null;
  }, 120_000, 'server URL');
  return {
    url,
    stop() {
      child.kill('SIGTERM');
    },
  };
}

async function measureImport(baseUrl, sourceDir, firstWorldCount, timeoutMs) {
  const start = performance.now();
  await postJson(new URL('api/import/folder', baseUrl), { path: sourceDir, background: true });
  let firstUsableWorldMs = null;
  let status = null;
  while (performance.now() - start < timeoutMs) {
    status = await getJson(new URL('api/status', baseUrl));
    if (firstUsableWorldMs === null && Number(status.totalAssets ?? 0) >= firstWorldCount) {
      firstUsableWorldMs = performance.now() - start;
    }
    if (status.jobPhase === 'ready' || status.state === 'idle' && Number(status.totalAssets ?? 0) > 0) {
      return {
        timeToImportCompleteMs: Math.round(performance.now() - start),
        timeToFirstUsableWorldMs: firstUsableWorldMs === null ? null : Math.round(firstUsableWorldMs),
        finalTotalAssets: status.totalAssets,
        finalStatus: status,
      };
    }
    if (status.jobPhase === 'error' || status.state === 'error') throw new Error(`import failed: ${status.jobMessage ?? 'unknown'}`);
    await delay(500);
  }
  throw new Error(`import did not complete within ${timeoutMs}ms; last status=${JSON.stringify(status)}`);
}

async function measureAtlas(baseUrl) {
  const start = performance.now();
  const atlas = await getJson(new URL('api/atlas/index.json', baseUrl));
  return {
    atlasBuildMs: Math.round(performance.now() - start),
    total: atlas.total,
    pageCount: atlas.pageCount,
    pageCapacity: atlas.pageCapacity,
    pageSize: atlas.pageSize,
    thumbSize: atlas.thumbSize,
  };
}

async function measurePlayview(baseUrl, visibleCount, timeoutMs, headed) {
  const browser = await chromium.launch({ headless: !headed });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const start = performance.now();
  try {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    await page.waitForFunction(() => typeof window.imageGardenDebug === 'function', null, { timeout: timeoutMs });
    await page.waitForFunction(() => window.imageGardenDebug?.().viewer?.imageCount > 0, null, { timeout: timeoutMs });
    const metadataMs = performance.now() - start;
    await page.waitForFunction(() => {
      const debug = window.imageGardenDebug?.();
      return (debug?.viewer?.lod?.loadedCards ?? debug?.viewer?.imageCount ?? 0) >= 1;
    }, null, { timeout: timeoutMs });
    const firstThumbnailMs = performance.now() - start;
    await page.waitForFunction((count) => {
      const debug = window.imageGardenDebug?.();
      return (debug?.viewer?.lod?.loadedCards ?? debug?.viewer?.imageCount ?? 0) >= count;
    }, visibleCount, { timeout: timeoutMs });
    const visibleMs = performance.now() - start;
    const debug = await page.evaluate(() => window.imageGardenDebug?.());
    return {
      bootToAllMetadataMs: Math.round(metadataMs),
      bootToFirstThumbnailMs: Math.round(firstThumbnailMs),
      bootToVisibleThumbnailsMs: Math.round(visibleMs),
      visibleThreshold: visibleCount,
      finalDebug: debug,
    };
  } finally {
    await browser.close();
  }
}

function visibleCards() {
  const debug = window.imageGardenDebug?.();
  return debug?.viewer?.lod?.loadedCards ?? debug?.viewer?.imageCount ?? 0;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`GET ${url} failed: ${response.status} ${response.statusText}`);
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok || body.ok === false) throw new Error(`POST ${url} failed: ${body.error ?? response.statusText}`);
  return body;
}

async function waitFor(readValue, timeoutMs, label) {
  const start = performance.now();
  while (performance.now() - start < timeoutMs) {
    const value = readValue();
    if (value) return value;
    await delay(100);
  }
  throw new Error(`timed out waiting for ${label}`);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function printResult(result) {
  console.log('\nBenchmark summary');
  console.log('=================');
  console.log(`dataDir: ${result.dataDir}`);
  if (result.sourceDir) console.log(`sourceDir: ${result.sourceDir}`);
  const importResult = result.importBackend;
  if (importResult.timeToImportCompleteMs !== undefined) {
    console.log(`import complete: ${formatMs(importResult.timeToImportCompleteMs)}`);
    console.log(`first usable world (${result.firstWorldCount}): ${formatNullableMs(importResult.timeToFirstUsableWorldMs)}`);
  }
  if (importResult.atlasBuild) {
    console.log(`atlas build: ${formatMs(importResult.atlasBuild.atlasBuildMs)} (${importResult.atlasBuild.pageCount} pages, ${importResult.atlasBuild.total} assets)`);
  }
  const playview = result.playviewLoading;
  console.log(`metadata loaded: ${formatMs(playview.bootToAllMetadataMs)}`);
  console.log(`first thumbnail: ${formatMs(playview.bootToFirstThumbnailMs)}`);
  console.log(`${playview.visibleThreshold} visible thumbnails: ${formatMs(playview.bootToVisibleThumbnailsMs)}`);
  const lod = playview.finalDebug?.viewer?.lod;
  const resources = playview.finalDebug?.resources;
  if (lod?.textureArrayPagesLoaded !== undefined) console.log(`texture-array pages loaded: ${lod.textureArrayPagesLoaded}`);
  if (lod?.atlasPagesLoaded !== undefined) console.log(`atlas pages loaded: ${lod.atlasPagesLoaded}`);
  if (resources?.textureArrayPageRequests !== undefined) console.log(`texture-array page requests: ${resources.textureArrayPageRequests}`);
  if (resources?.atlasPageRequests !== undefined) console.log(`atlas page requests: ${resources.atlasPageRequests}`);
  if (resources?.thumbnailRequests !== undefined) console.log(`thumbnail requests: ${resources.thumbnailRequests}`);
  console.log('\nJSON');
  console.log(JSON.stringify(result, null, 2));
}

function formatMs(value) {
  return `${value}ms`;
}

function formatNullableMs(value) {
  return value === null || value === undefined ? 'n/a' : formatMs(value);
}
