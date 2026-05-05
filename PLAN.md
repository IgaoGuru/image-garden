# Constellation — Image Embedding 3D Explorer

## Overview

A flyable 3D space where images float as a constellation, positioned by their CLIP embeddings reduced to 3D via UMAP. Users navigate with WASD + mouse (FPS-style) and discover visual similarity clusters.

## Architecture

Two separate pieces of software:

### 1. `@constellation/viewer` — npm-publishable library

**Input:** `Array<{ id, url, embedding: number[] }>` + config options
**Output:** A mountable 3D scene

```
packages/viewer/
├── src/
│   ├── index.ts       # public API: mount(container, data, opts) / destroy()
│   ├── scene.ts       # Three.js scene, camera, renderer, resize handling
│   ├── controls.ts    # PointerLockControls + WASD + shift-to-sprint
│   ├── layout.ts      # UMAP reduction (embedding[] → Vec3[]) via umap-js
│   ├── sprites.ts     # individual PlaneGeometry + MeshBasicMaterial per image
│   └── loader.ts      # lazy image loading by proximity
├── package.json
├── tsconfig.json
└── vite.config.ts     # library build mode
```

- Framework-agnostic (vanilla JS, optional React wrapper later)
- No opinions on how embeddings are generated
- Individual sprite planes (not instanced mesh) — simple, fine for <5k images
- Thumbnails (256x256) in 3D view, full-res on click/hover only

### 2. `constellation-studio` — local CLI tool (not published)

For personal use to generate and manage `<image, embedding>` pairs.

```
studio/
├── embed.py           # walks dir, runs CLIP (open_clip), outputs JSON
├── serve.py           # serves images + viewer demo, opens browser
└── pyproject.toml
```

- Uses `uv` with `~/.venvs/ml` venv (already exists on machine)
- Python + open_clip for CLIP embeddings
- CLI: `python studio/embed.py ./photos` → JSON file → `python studio/serve.py`

## Monorepo Structure

```
constellation/  (this repo: /Users/igorrocha/Documents/Projects/photoview)
├── packages/
│   └── viewer/          # @constellation/viewer
├── studio/              # local CLI tool
├── pnpm-workspace.yaml
├── package.json         # root
└── PLAN.md              # this file
```

- pnpm workspaces for JS
- TypeScript strict
- uv for Python

## Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| 3D engine | Three.js | Standard, handles textured quads well |
| FPS controls | PointerLockControls + WASD | Built into Three.js |
| Dim reduction | UMAP via `umap-js` | Best local+global structure preservation |
| Embedding model | CLIP via `open_clip` (Python) | Fast on M1, good quality |
| Image sprites | Individual PlaneGeometry | Simple, fine for <5k images |
| JS build | Vite library mode | Fast, good DX |
| Monorepo | pnpm workspaces | Already uses pnpm |
| Python tooling | uv | User preference |

## Performance Expectations (M1 MBP 16GB)

- **Embedding 1k 1080p images:** ~1-2 min (GPU) to ~3-8 min (CPU)
- **UMAP 1k points 512d→3d:** ~2-5 seconds
- **Rendering 1k sprites:** 60fps easily, ~250MB VRAM with 256x256 thumbnails

## Prior Art / Research

No existing project combines 3D fly-through + images at embedding positions + embeddable npm library.

Closest existing projects:
- **scatter-gl** (Google PAIR, 191 stars) — npm, 3D, sprite support, but orbit controls not FPS
- **threejs-instancing-images** (9 stars) — clean ~300-line Svelte reference, not a library
- **Apple Embedding Atlas** (4.7k stars) — polished npm package, but 2D only
- **PixPlot** (Yale DHLab, 643 stars) — OG image constellation, generates static sites, 2D only
- **Deepscatter** (Nomic, 1.1k stars) — scales to billions, npm, but 2D only

## Task Breakdown (2 parallel lanes)

### Lane 1 — Viewer library (packages/viewer/)
1. Scaffold pnpm monorepo + packages/viewer with Vite in library mode
2. Three.js scene + renderer + resize handling
3. FPS controls (PointerLockControls + WASD + shift-to-sprint)
4. UMAP layout engine (umap-js, embeddings → 3D positions)
5. Sprite planes — load images as textured quads at UMAP positions
6. Proximity-based lazy loading (load textures for nearby images first)
7. Public API: mount(el, data, opts) / destroy()
8. Demo HTML page consuming the library

### Lane 2 — Studio CLI (studio/)
1. embed.py — walks directory, runs CLIP (open_clip), outputs JSON
2. serve.py — serves images + viewer demo, opens browser
3. Wire together: embed → JSON → serve → viewer

### Data Contract (shared between viewer and studio)

```typescript
interface ConstellationData {
  images: Array<{
    id: string;
    url: string;           // relative or absolute URL to image
    embedding: number[];   // high-dimensional CLIP embedding (512d or 768d)
  }>;
}
```

Studio outputs this JSON. Viewer consumes it.
