# @constellation/viewer

Flyable Three.js viewer for positioned photo constellations.

The product/runtime path consumes precomputed `RuntimeAsset` records through a mockable `ConstellationDataSource` boundary. Embedding-based browser UMAP remains available as a developer/demo fallback, but runtime viewers should not require embeddings.

```ts
import {
  createStaticDataSource,
  mountFromDataSource,
  type RuntimeAsset,
} from '@constellation/viewer';

const assets: RuntimeAsset[] = [
  {
    id: 'image-1',
    thumbnailUrl: '/api/thumbnails/image-1',
    fullUrl: '/api/files/image-1',
    position: [0, 0, 0],
    metadata: { sourcePath: '/Photos/image-1.jpg' },
  },
];

const source = createStaticDataSource(assets);
const viewer = await mountFromDataSource(document.querySelector('#app')!, source, {
  layout: { center: false },
  sprites: { renderMode: 'auto' },
  onSelect: (image) => console.log(image),
});

// later
viewer.destroy();
```

For a local HTTP backend implementing the roadmap API:

```ts
import { createFetchDataSource, mountFromDataSource } from '@constellation/viewer';

await mountFromDataSource(el, createFetchDataSource({ baseUrl: 'http://127.0.0.1:8000' }));
```

## Legacy/direct mount

Existing direct mounting is preserved:

```ts
import { mount, type ConstellationData } from '@constellation/viewer';

const data: ConstellationData = {
  images: [
    {
      id: 'image-1',
      url: '/photos/image-1.jpg',
      thumbnailUrl: '/thumbs/image-1.jpg',
      embedding: [/* CLIP vector */],
      // position: [0, 0, 0], // preferred when available
    },
  ],
};

mount(el, data, { layout: { scale: 160 } });
```

## Controls

- Click the canvas to lock pointer.
- WASD / arrows: fly relative to current look direction.
- Space or E: world-up.
- Q or C: world-down.
- Shift: sprint.
- Esc: unlock pointer.

## Data contract

Runtime assets provide `id`, `thumbnailUrl`, optional `fullUrl`, optional size/metadata, and required precomputed `position: [x, y, z]`.

Legacy `ConstellationData` images can provide either:

- `position`: precomputed `[x, y, z]`; centered by default and otherwise preserves its units unless `layout.scale` is provided, or
- `embedding`: high-dimensional vector; the viewer runs UMAP in-browser as a developer fallback.

## Scalable rendering path

`sprites.renderMode: 'lod'` renders every asset as a cheap point cloud and promotes nearby/selected assets to textured cards. It also evicts distant non-selected cards. `renderMode: 'auto'` switches to LOD above `lodThreshold` assets.

Useful knobs:

```ts
mountFromDataSource(el, source, {
  sprites: {
    renderMode: 'lod',
    pointSize: 3,
    lazyLoadDistance: 260,
    textureUnloadDistance: 360,
    maxTexturedCards: 180,
  },
});
```

Embedding-derived layouts still run a small 3D collision relaxation pass by default. The viewport-height cap is an emergency close-up guard; set `maxViewportHeight: Infinity` to disable it.
