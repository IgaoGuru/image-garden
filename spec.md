# Constellation Architecture Spec

Constellation is a local-first photo exploration system. It is intentionally structured as a pipeline of replaceable modules connected by stable data contracts.

The core product promise is:

```text
bring your own photos
→ process them locally
→ generate embeddings and layout
→ explore them in a 3D viewer
```

The project should not be designed as one tightly coupled application. Importing, image processing, embedding, layout, storage, UI, and rendering must remain separable.

---

## Architectural principles

### 1. Local-first by default

Photos should stay on the user's machine unless the user explicitly opts into a future cloud embedding provider.

The default consumer flow should use local processing and local storage.

### 2. Bring-your-own photos

The app supports user-provided photo inputs:

- local photo directories;
- Constellation Studio datasets.

Cloud photo account connectors are not part of the current product scope.

### 3. Viewer is only a renderer

`@constellation/viewer` must remain a reusable renderer. It consumes positioned runtime assets and renders them.

It must not own:

- onboarding;
- filesystem access;
- source importing;
- image normalization;
- embedding generation;
- indexing jobs;
- persistent storage.

### 4. Embeddings are backend data

Embeddings are produced and cached by Studio/backend modules. They are not required by the runtime viewer path.

The viewer should primarily consume precomputed positions.

### 5. Heavy work belongs behind interchangeable providers

The inference engine, layout engine, asset processor, and source adapters should be swappable without changing the whole app.

---

## Core module pipeline

```text
Source
  → Asset normalization
  → Inference engine
  → Embedding cache
  → Layout engine
  → Catalog / index store
  → Local app API
  → App UI
  → Viewer
```

Each step should communicate through explicit data structures rather than implementation details.

---

## Module semantics

## 1. Source module

The source module answers:

```text
Where do images come from?
```

Current source types:

- photo directory;
- Constellation Studio dataset.

Possible future source types:

- zip/archive import;
- external drive import;
- other file-backed datasets.

A source adapter scans an input and produces source-level asset records. These records describe discovered media before Constellation owns or normalizes the files.

The rest of the system should not care whether an image came from a directory or a Studio dataset.

---

## 2. Asset normalization module

The asset normalization module answers:

```text
How do raw source files become app-owned assets?
```

Responsibilities:

- read original image files;
- apply orientation/EXIF correction;
- decode supported input formats;
- create browser-friendly canonical images;
- create thumbnails;
- assign stable asset IDs;
- write generated assets into the local workspace.

Possible implementations:

- Pillow-based processor;
- libvips-based processor;
- native platform image processor.

This module outputs normalized assets that downstream modules can embed, index, and serve.

---

## 3. Inference engine module

The inference engine answers:

```text
How do images become embedding vectors?
```

This is a provider boundary.

Planned implementations:

- ONNX Runtime engine for consumer/local default;
- PyTorch/OpenCLIP engine for development and advanced users;
- future BYO cloud API engine;
- future custom HTTP embedding endpoint.

All implementations should satisfy the same semantic contract:

```text
image batch → embedding vector batch
```

The rest of the app should not know or care which engine produced the vectors.

---

## 4. Embedding cache module

The embedding cache answers:

```text
Have we already embedded this asset with this model and configuration?
```

Responsibilities:

- cache embeddings by asset identity;
- include model, engine, and preprocessing settings in cache keys;
- avoid unnecessary recomputation;
- allow multiple embedding engines/models to coexist safely.

Changing from ONNX to PyTorch, or from one model to another, must not corrupt prior cached data.

---

## 5. Layout engine module

The layout engine answers:

```text
Where should each image appear in 3D space?
```

Inputs:

- embedding vectors; or
- precomputed positions from a dataset.

Outputs:

- 3D positions for runtime assets.

Possible implementations:

- UMAP;
- imported/precomputed Studio layout.

No deterministic/random fallback layout is allowed for real user imports. If embeddings or precomputed positions are unavailable, the import must fail clearly instead of placing images on the map.

The viewer consumes positions, not layout algorithm internals.

---

## 6. Catalog / index store module

The catalog/index store answers:

```text
What does Constellation know about the local library?
```

Responsibilities:

- persist sources;
- persist assets;
- persist normalized file paths;
- persist metadata;
- persist layout positions;
- persist job/indexing state;
- support runtime asset queries.

SQLite is the current persistence layer, but callers should depend on store semantics rather than SQLite implementation details.

---

## 7. Job runner module

The job runner answers:

```text
How do long-running indexing tasks execute safely?
```

Responsibilities:

- scan sources;
- normalize assets;
- compute embeddings;
- compute layout;
- update progress;
- support safe restart/resume where possible;
- isolate long-running work from the UI.

Possible implementations:

- single-process local runner;
- multiprocess local worker;
- native GPU worker;
- future cloud worker.

The app UI should observe job state, not perform job work itself.

---

## 8. Local app API module

The local app API answers:

```text
How does the UI communicate with the local backend?
```

Responsibilities:

- expose library status;
- expose available source types;
- trigger imports/indexing;
- expose runtime assets;
- serve generated local assets;
- expose job progress.

This boundary allows the frontend/UI to evolve independently from backend indexing internals.

---

## 9. App UI module

The app UI answers:

```text
What does the user interact with?
```

Responsibilities:

- onboarding;
- source selection;
- progress display;
- import controls;
- error/recovery messaging;
- transition into the viewer;
- adding more sources later.

The app UI talks to the local app API. It should not directly own embedding, indexing, or image processing logic.

---

## 10. Viewer module

The viewer answers:

```text
How do positioned assets become an interactive 3D constellation?
```

Responsibilities:

- render a 3D scene;
- handle camera/navigation controls;
- render image sprites/cards/LOD;
- support hover/selection callbacks;
- consume runtime assets.

The viewer is downstream of all import/indexing work.

Its primary input is the runtime asset contract.

---

## Runtime asset contract

The core runtime object passed into the viewer has this semantic shape:

```text
RuntimeAsset
  id
  thumbnail URL
  optional full image URL
  optional width/height
  3D position
  optional metadata
```

This contract intentionally excludes embeddings. Embeddings are useful for indexing and layout, but not required for runtime rendering.

---

## Interchangeable boundaries

The most important replaceable boundaries are:

```text
SourceAdapter
AssetProcessor
EmbeddingProvider
EmbeddingCache
LayoutProvider
IndexStore
JobRunner
RuntimeDataSource
```

These boundaries should remain explicit as the project evolves.

Examples of valid substitutions:

```text
ONNX inference ↔ PyTorch/OpenCLIP inference ↔ BYO cloud inference
Pillow processing ↔ libvips processing ↔ native image processing
UMAP layout ↔ future custom semantic layout
folder source ↔ Studio dataset source ↔ archive source
local browser UI ↔ future native shell
```

---

## Current repository mapping

```text
studio/src/constellation_studio/
  source_adapters.py   Source module
  assets.py            Asset normalization
  app.py               Browser-first local app launcher
  embed.py             Embedding CLI/pipeline entrypoints
  embedding_providers.py  EmbeddingProvider boundary and local providers
  cache.py             Embedding cache
  layout.py            Backend layout projection helpers
  indexing.py          Import/index pipeline and layout orchestration
  index_store.py       SQLite catalog/index store
  backend.py           Local backend and local app UI serving
  schema.py            Shared Studio JSON/data contracts

packages/viewer/src/
  index.ts             Viewer public API
  data-source.ts       Runtime data source helpers
  scene.ts             Three.js scene host
  sprites.ts           Sprite/card rendering
  lod.ts               Large-library render strategy
  controls.ts          Navigation controls
  layout.ts            Browser/demo layout utilities

apps/desktop/
  Thin Electron shell; optional long-term if the local browser app becomes the primary distribution model.
```

---

## Strategic direction

The preferred near-term architecture is:

```text
one-command installer
→ local backend + local browser UI
→ local ONNX embedding engine by default
→ optional PyTorch/OpenCLIP engine for advanced users
→ positioned runtime assets
→ reusable viewer
```

This preserves privacy, avoids cloud inference costs, and keeps the codebase modular enough to add other embedding engines or app shells later.
