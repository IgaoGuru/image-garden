# @constellation/viewer

Flyable Three.js viewer for image embedding constellations.

```ts
import { mount, type ConstellationData } from '@constellation/viewer';

const data: ConstellationData = {
  images: [
    {
      id: 'image-1',
      url: '/photos/image-1.jpg',
      thumbnailUrl: '/thumbs/image-1.jpg',
      embedding: [/* CLIP vector */],
      // position: [0, 0, 0], // optional precomputed 3D position instead of embedding
    },
  ],
};

const viewer = mount(document.querySelector('#app')!, data, {
  layout: { scale: 160 },
  sprites: { size: 8 },
  onSelect: (image) => console.log(image),
});

// later
viewer.destroy();
```

## Controls

- Click the canvas to lock pointer.
- WASD / arrows: fly relative to current look direction.
- Space or E: world-up.
- Q or C: world-down.
- Shift: sprint.
- Esc: unlock pointer.

## Data contract

Each image can provide either:

- `embedding`: high-dimensional vector; the viewer runs UMAP in-browser and normalizes the result to `layout.scale` world units, or
- `position`: precomputed `[x, y, z]`; the viewer centers it by default and otherwise preserves its units unless `layout.scale` is provided.

`thumbnailUrl` is preferred for scene textures. `url` is the fallback/full image URL.

## Cluster readability

Embedding-derived layouts run a small 3D collision relaxation pass by default. Nearby points are pushed apart while being weakly pulled back toward their original UMAP positions, reducing sprite pileups without encoding density as image size.

Useful knobs:

```ts
mount(el, data, {
  layout: {
    collisionRelaxation: true,
    collisionDistance: 10,
    collisionIterations: 35,
    collisionAnchorStrength: 0.025,
  },
  sprites: {
    maxViewportHeight: 0.45,
  },
});
```

The viewport-height cap is now only an emergency close-up guard: by default, a sprite can occupy at most 45% of the viewport height. Set `maxViewportHeight: Infinity` to disable it.
