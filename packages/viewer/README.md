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

## Close-up sprite stability

By default, sprites are capped to `sprites.maxViewportHeight = 0.22`, meaning an image can occupy at most 22% of the viewport height. When you fly very close to a dense cluster, nearby images shrink just enough to keep that observed height stable, revealing neighboring images instead of filling the whole screen. When you back away, they return to normal world size. Set `maxViewportHeight: Infinity` to disable this behavior.
