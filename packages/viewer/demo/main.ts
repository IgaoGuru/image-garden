import { mount, type ConstellationData } from '../src';

const categories = [
  { name: 'forest', color: '#4ade80', vector: [1, 0, 0, 0, 0, 0] },
  { name: 'ocean', color: '#38bdf8', vector: [0, 1, 0, 0, 0, 0] },
  { name: 'sunset', color: '#fb923c', vector: [0, 0, 1, 0, 0, 0] },
  { name: 'city', color: '#a78bfa', vector: [0, 0, 0, 1, 0, 0] },
  { name: 'snow', color: '#e5e7eb', vector: [0, 0, 0, 0, 1, 0] },
  { name: 'flowers', color: '#f472b6', vector: [0, 0, 0, 0, 0, 1] },
] as const;

const data: ConstellationData = {
  images: Array.from({ length: 180 }, (_, index) => {
    const category = categories[index % categories.length] ?? categories[0];
    const embedding = category.vector.flatMap((value, dim) => [
      value + Math.sin(index * 0.13 + dim) * 0.06,
      value * 0.5 + Math.cos(index * 0.17 + dim) * 0.06,
      Math.sin(index * 0.07 + dim) * 0.03,
      Math.cos(index * 0.11 + dim) * 0.03,
    ]);
    return {
      id: `demo-${index}`,
      url: makeSvgDataUrl(category.name, category.color, index),
      thumbnailUrl: makeSvgDataUrl(category.name, category.color, index),
      embedding,
      width: 256,
      height: 256,
      metadata: { category: category.name },
    };
  }),
};

const selected = document.querySelector<HTMLParagraphElement>('#selected');
const app = document.querySelector<HTMLElement>('#app');
if (!app) throw new Error('Missing #app element');

mount(app, data, {
  backgroundColor: 0x05050a,
  layout: { scale: 180, seed: 7, nNeighbors: 12, minDist: 0.12 },
  sprites: { size: 10, lazyLoadDistance: 260, maxLoadedTextures: 240 },
  controls: { moveSpeed: 60, sprintMultiplier: 3 },
  onSelect: (image) => {
    if (selected) {
      selected.textContent = `Selected ${image.id} (${String(image.metadata?.category ?? 'unknown')})`;
    }
  },
});

function makeSvgDataUrl(label: string, color: string, index: number): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <radialGradient id="g" cx="35%" cy="25%" r="80%">
      <stop offset="0" stop-color="white" stop-opacity="0.9"/>
      <stop offset="0.4" stop-color="${color}"/>
      <stop offset="1" stop-color="#111827"/>
    </radialGradient>
  </defs>
  <rect width="256" height="256" rx="24" fill="url(#g)"/>
  <circle cx="${48 + (index * 17) % 160}" cy="${44 + (index * 29) % 160}" r="${18 + (index % 5) * 5}" fill="white" fill-opacity="0.22"/>
  <text x="128" y="120" text-anchor="middle" font-family="system-ui, sans-serif" font-size="28" font-weight="700" fill="white">${label}</text>
  <text x="128" y="154" text-anchor="middle" font-family="system-ui, sans-serif" font-size="18" fill="white" fill-opacity="0.8">#${index}</text>
</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
