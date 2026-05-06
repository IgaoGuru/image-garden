import { computeLayout, mount, relaxCollisions } from '../dist/constellation-viewer.js';

if (typeof mount !== 'function') {
  throw new Error('Expected mount export to be a function.');
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

console.log('viewer smoke checks passed');
