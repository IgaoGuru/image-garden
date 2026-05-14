import { mount } from '@image-garden/viewer';

// Runtime applications should usually provide precomputed positions.
// Embedding fallback is useful for demos and experiments.
mount(document.querySelector<HTMLElement>('#app')!, {
  images: [
    { id: 'red', thumbnailUrl: '/red.jpg', embedding: [1, 0, 0, 0] },
    { id: 'green', thumbnailUrl: '/green.jpg', embedding: [0, 1, 0, 0] },
    { id: 'blue', thumbnailUrl: '/blue.jpg', embedding: [0, 0, 1, 0] },
    { id: 'dark', thumbnailUrl: '/dark.jpg', embedding: [0, 0, 0, 1] },
  ],
}, {
  layout: { scale: 160, seed: 42 },
});
