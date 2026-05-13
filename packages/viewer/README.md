# @image-garden/viewer

Flyable Three.js viewer for positioned image maps.

This package is the reusable rendering layer. It does not import folders, generate embeddings, own Image Garden menus, or require the Image Garden local backend. The product/runtime path consumes precomputed `RuntimeAsset` records through a mockable `ConstellationDataSource` boundary. Embedding-based browser UMAP remains available as a developer/demo fallback, but runtime viewers should not require embeddings.

```ts
import {
  createStaticDataSource,
  mountFromDataSource,
  type RuntimeAsset,
} from '@image-garden/viewer';

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

For a local HTTP backend implementing the Studio/Image Garden API adapter:

```ts
import { createStudioDataSource, mountFromDataSource } from '@image-garden/viewer';

await mountFromDataSource(el, createStudioDataSource({ baseUrl: 'http://127.0.0.1:8000' }));
```

Custom hosts can keep the same parser and provide explicit endpoint paths:

```ts
import { createFetchDataSource } from '@image-garden/viewer';

createFetchDataSource({
  baseUrl: 'https://example.test',
  endpoints: {
    status: '/viewer/status.json',
    assets: '/viewer/assets.json',
    nearAssets: '/viewer/near-assets',
    asset: (id) => `/viewer/assets/${encodeURIComponent(id)}.json`,
  },
});
```

## Legacy/direct mount

Existing direct mounting is preserved:

```ts
import { mount, type ConstellationData } from '@image-garden/viewer';

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

## API docs and examples

- `docs/api.md` describes the public API, data-source contract, callbacks, and rendering options.
- `examples/static-assets.html` shows direct mounting with positioned image URLs.
- `examples/custom-data-source.ts` shows a custom HTTP adapter.
- `examples/embedding-fallback.ts` shows in-browser embedding layout for experiments.
- `examples/atlas-texture-array-host/` documents the experimental acceleration manifest formats.

## Controls

- Click the canvas to lock pointer.
- WASD / arrows: fly relative to current look direction.
- Space or E: world-up.
- Q or C: world-down.
- Shift: fast movement.
- Esc: unlock pointer.

## Data contract

Runtime assets provide `id`, `thumbnailUrl`, optional `fullUrl`, optional size/metadata, and required precomputed `position: [x, y, z]`.

Legacy `ConstellationData` images can provide either:

- `position`: precomputed `[x, y, z]`; centered by default and otherwise preserves its units unless `layout.scale` is provided, or
- `embedding`: high-dimensional vector; the viewer runs UMAP in-browser as a developer fallback.

## Scalable rendering path

`sprites.renderMode: 'lod'` renders every asset as a cheap point cloud and promotes nearby/selected assets to textured cards. It also evicts distant non-selected cards. `renderMode: 'auto'` switches to LOD above `lodThreshold` assets.

The generic LOD path loads individual thumbnail URLs. Atlas and texture-array acceleration are opt-in and require explicit index URLs supplied by the host application.

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

```ts
mountFromDataSource(el, source, {
  sprites: {
    renderMode: 'lod',
    textureArray: true,
    textureArrayIndexUrl: '/my-texture-pages/index.json',
  },
});
```

Embedding-derived layouts still run a small 3D collision relaxation pass by default. The viewport-height cap is an emergency close-up guard; set `maxViewportHeight: Infinity` to disable it.
