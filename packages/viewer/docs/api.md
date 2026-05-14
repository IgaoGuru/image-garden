# @image-garden/viewer API

`@image-garden/viewer` is the reusable rendering package for flyable image maps. It does not import folders, generate embeddings, or require Image Garden Studio.

## Core mount API

```ts
import { mount } from '@image-garden/viewer';

const viewer = mount(container, { images }, options);
viewer.fitToContent();
viewer.setSelected('image-id');
viewer.destroy();
```

`mount()` accepts `ConstellationData`, a legacy-compatible shape whose `images` can contain either precomputed `position` coordinates or high-dimensional `embedding` vectors. Precomputed positions are preferred for runtime apps.

## RuntimeAsset

```ts
interface RuntimeAsset {
  id: string;
  thumbnailUrl: string;
  fullUrl?: string;
  width?: number;
  height?: number;
  position: [number, number, number];
  metadata?: Record<string, unknown>;
}
```

`RuntimeAsset` is the recommended app/runtime contract. Embeddings are intentionally absent; indexing systems should compute and persist positions before handing data to the viewer.

## Data sources

```ts
interface ConstellationDataSource {
  getStatus(): Promise<IndexStatus>;
  getInitialAssets(): Promise<RuntimeAsset[]>;
  getNearbyAssets?(query: NearbyQuery): Promise<RuntimeAsset[]>;
  getAsset?(id: string): Promise<RuntimeAsset | null>;
}
```

Use `createStaticDataSource()` for local arrays, `createFetchDataSource()` for custom HTTP APIs, or `createStudioDataSource()` for the Image Garden Studio API adapter.

## SpriteOptions

Important rendering options:

- `renderMode`: `cards`, `lod`, or `auto`.
- `lodThreshold`: dataset size where `auto` switches to LOD.
- `lazyLoadDistance`: world distance for textured-card promotion.
- `textureUnloadDistance`: distance for evicting cards.
- `maxTexturedCards`: cap on materialized textured cards.
- `minCardScreenHeightPx`: keeps tiny far images as points.
- `frustumCullCards` and `frustumCullMargin`: cull offscreen cards with optional margin.
- `textureArray` and `textureArrayIndexUrl`: experimental texture-array acceleration.
- `atlas` and `atlasIndexUrl`: experimental atlas acceleration.

The generic LOD path works from each image's `thumbnailUrl`. Atlas and texture-array modes are opt-in and require host-provided manifest URLs.

## Callbacks

```ts
mount(container, data, {
  onSelect: (image) => console.log('selected', image.id),
  onHover: (image) => console.log('hovered', image?.id),
  onReady: (viewer) => viewer.fitToContent(),
});
```

Callbacks receive positioned `ConstellationImage` records.

## Experimental manifest formats

Atlas and texture-array manifests are public enough for Image Garden/Playview integration, but still marked experimental. They may change before a stable 1.0 release.

Texture-array manifests currently describe fixed-size JPEG grid pages that the browser slices into WebGL `DataArrayTexture` layers. Atlas manifests describe page images plus per-image UV rectangles. See `examples/atlas-texture-array-host` for the expected shape.
