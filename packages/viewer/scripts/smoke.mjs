import { computeLayout, mount } from '../dist/constellation-viewer.js';

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
