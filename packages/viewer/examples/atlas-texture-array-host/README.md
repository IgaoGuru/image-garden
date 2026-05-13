# Atlas / texture-array host example

Atlas and texture-array acceleration are experimental host-provided formats. The viewer does not assume where these files live; pass URLs explicitly.

```ts
import { mountFromDataSource, createStaticDataSource } from '@image-garden/viewer';

await mountFromDataSource(el, createStaticDataSource(assets), {
  sprites: {
    renderMode: 'lod',
    textureArray: true,
    textureArrayIndexUrl: '/texture-array/index.json?thumbSize=128&layersPerPage=256',
    atlas: true,
    atlasIndexUrl: '/atlas/index.json',
  },
});
```

Texture-array index shape:

```json
{
  "format": "rgba8-grid-jpeg",
  "thumbSize": 128,
  "layersPerPage": 256,
  "cols": 16,
  "rows": 16,
  "pages": [
    { "index": 0, "url": "/texture-array/page-0.jpg", "layers": 256 }
  ],
  "entries": [
    { "id": "asset-id", "page": 0, "layer": 0 }
  ]
}
```

Atlas index shape:

```json
{
  "thumbSize": 128,
  "pageSize": 4096,
  "pageCapacity": 1024,
  "pages": [
    { "index": 0, "url": "/atlas/page-0.jpg", "width": 4096, "height": 4096 }
  ],
  "entries": [
    { "id": "asset-id", "page": 0, "u0": 0, "v0": 0, "u1": 0.03125, "v1": 0.03125 }
  ]
}
```

Prefer the generic thumbnail-URL LOD path unless you need very large visible-card counts.
