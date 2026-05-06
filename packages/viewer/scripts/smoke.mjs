import {
  computeLayout,
  createFetchDataSource,
  createStaticDataSource,
  createSyntheticRuntimeAssets,
  mount,
  mountFromDataSource,
  relaxCollisions,
  runtimeAssetsToData,
} from '../dist/constellation-viewer.js';

if (typeof mount !== 'function') {
  throw new Error('Expected mount export to be a function.');
}
if (
  typeof mountFromDataSource !== 'function' ||
  typeof createStaticDataSource !== 'function' ||
  typeof createFetchDataSource !== 'function'
) {
  throw new Error('Expected data-source exports to be available.');
}

const positioned = computeLayout({
  images: [
    { id: 'a', url: 'a.jpg', position: [10, 0, 0] },
    { id: 'b', url: 'b.jpg', position: [20, 0, 0] },
  ],
});
const positionedXs = positioned.map((image) => image.position[0]);
if (positionedXs[0] !== -5 || positionedXs[1] !== 5) {
  throw new Error(`Precomputed positions should be centered without default scaling; got ${positionedXs.join(',')}`);
}

const relaxed = relaxCollisions(
  [
    [0, 0, 0],
    [0, 0, 0],
  ],
  { collisionDistance: 10, collisionIterations: 20, collisionAnchorStrength: 0 },
);
const dx = relaxed[0][0] - relaxed[1][0];
const dy = relaxed[0][1] - relaxed[1][1];
const dz = relaxed[0][2] - relaxed[1][2];
const relaxedDistance = Math.hypot(dx, dy, dz);
if (relaxedDistance < 9.9) {
  throw new Error(`Collision relaxation failed; distance=${relaxedDistance}`);
}

const embedded = computeLayout({
  images: [
    { id: 'a', url: 'a.jpg', embedding: [1, 0, 0] },
    { id: 'b', url: 'b.jpg', embedding: [0, 1, 0] },
    { id: 'c', url: 'c.jpg', embedding: [0, 0, 1] },
  ],
});
if (embedded.length !== 3 || !embedded.every((image) => image.position.length === 3)) {
  throw new Error('Embedding layout failed to produce 3D positions.');
}

const runtimeAssets = createSyntheticRuntimeAssets({ count: 32, radius: 10 });
const runtimeData = runtimeAssetsToData(runtimeAssets);
if (runtimeData.images.length !== 32 || runtimeData.images.some((image) => image.embedding !== undefined || !image.position)) {
  throw new Error('Runtime assets should convert to positioned, embedding-free viewer data.');
}
const staticSource = createStaticDataSource(runtimeAssets);
const nearby = await staticSource.getNearbyAssets({ x: 0, y: 0, z: 0, radius: 100, limit: 5 });
if (nearby.length !== 5 || nearby.some((asset) => !asset.position || !asset.thumbnailUrl)) {
  throw new Error('Static data source nearby query failed.');
}

let duplicateRejected = false;
try {
  createStaticDataSource([
    { id: 'dup', thumbnailUrl: 'a.jpg', position: [0, 0, 0] },
    { id: 'dup', thumbnailUrl: 'b.jpg', position: [1, 0, 0] },
  ]);
} catch {
  duplicateRejected = true;
}
if (!duplicateRejected) {
  throw new Error('Static data source should reject duplicate ids.');
}

const fetchCalls = [];
const fetchSource = createFetchDataSource({
  baseUrl: 'http://example.test',
  initialLimit: 10,
  fetch: async (url) => {
    const href = String(url);
    fetchCalls.push(href);
    if (href.endsWith('/api/status')) {
      return jsonResponse({ state: 'ready', totalAssets: 1, indexedAssets: 1 });
    }
    if (href.endsWith('/api/assets?limit=10')) {
      return jsonResponse({ assets: [{ id: 'remote-a', thumbnailUrl: '/thumb/a', position: [1, 2, 3] }] });
    }
    if (href.includes('/api/assets/near?')) {
      return jsonResponse([{ id: 'remote-near', thumbnailUrl: '/thumb/near', position: [0, 0, 0] }]);
    }
    if (href.endsWith('/api/assets/missing')) {
      return new Response('', { status: 404, statusText: 'Not Found' });
    }
    throw new Error(`Unexpected fetch URL: ${href}`);
  },
});
const remoteStatus = await fetchSource.getStatus();
const remoteAssets = await fetchSource.getInitialAssets();
const remoteNearby = await fetchSource.getNearbyAssets({ x: 0, y: 0, z: 0, radius: 10 });
const missingAsset = await fetchSource.getAsset('missing');
if (
  remoteStatus.state !== 'ready' ||
  remoteAssets[0]?.id !== 'remote-a' ||
  remoteNearby[0]?.id !== 'remote-near' ||
  missingAsset !== null ||
  !fetchCalls.includes('http://example.test/api/assets?limit=10')
) {
  throw new Error('Fetch data source contract checks failed.');
}

console.log('viewer smoke checks passed');

function jsonResponse(value) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}
