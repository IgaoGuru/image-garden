# Image Garden package boundaries

Image Garden is split into three cooperating packages/layers. The goal is to keep each layer useful on its own while preserving the current local app experience.

## Viewer

The viewer is the reusable rendering package.

It owns:

- Three/WebGL scene setup;
- fly controls;
- picking and selection callbacks;
- layout fallback from embeddings for demos/experiments;
- positioned-image rendering;
- point/card LOD;
- texture queueing;
- atlas and texture-array renderers when explicit page/index URLs are provided.

It should not own:

- folder import;
- local embedding generation;
- Studio job/status semantics;
- Image Garden onboarding/menu/audio/product UI;
- implicit assumptions that every host has `/api/assets`, `/api/atlas`, or `/api/texture-array`.

The viewer accepts direct `ConstellationData`, a `ConstellationDataSource`, or static `RuntimeAsset[]`. HTTP loading is an adapter: `createStudioDataSource()` names the Studio/Image Garden local API explicitly, while `createFetchDataSource()` lets other hosts provide their own endpoint paths.

## Studio

Studio is the local data and ML engine.

It owns:

- CLI/app startup;
- source folder/studio dataset import;
- image sanitization;
- thumbnail/full-image asset generation;
- embedding model setup and execution;
- embedding cache use;
- layout computation;
- SQLite index/state storage;
- local API routes for product clients;
- serving configured static assets in the release app.

It should avoid owning product UI behavior. Serving Playview/Viewer dist files is a deployment concern, not a reason for Studio to know Playview interaction details.

## Playview

Playview is the Image Garden product shell.

It owns:

- onboarding;
- import controls;
- status/progress display;
- menu/debug/audio UI;
- layout tuning controls;
- fetching/paginating Studio runtime assets;
- wiring Studio API URLs into Viewer options;
- Image Garden-specific rendering defaults.

It should remain free to be opinionated. It is not the generic viewer package; it is the app that composes Studio and Viewer into Image Garden.

## Current compatibility contracts

- Studio exposes `/api/status`, `/api/assets`, `/api/assets/near`, `/api/assets/:id`, `/api/thumbnails/:id`, and `/api/files/:id` for runtime assets.
- Texture-page acceleration is explicit from the Viewer perspective: Playview passes `/api/texture-array/index.json?...` and `/api/atlas/index.json` URLs into Viewer options.
- `@image-garden/viewer` can use `createStudioDataSource()` against the Studio API, but custom hosts should pass their own endpoint paths to `createFetchDataSource()` or use `createStaticDataSource()`.
- Playview remains responsible for Image Garden behavior and defaults; Viewer remains responsible for rendering those options efficiently.

## Remaining coupling allowed by design

Studio still serves configured Playview and Viewer static files in the packaged local app. This is acceptable as deployment glue: Studio should know where static assets are, but not how Playview menus, onboarding, audio, or layout tuning behave.

Viewer still includes `createStudioDataSource()` as a convenience adapter. This is acceptable as an explicit adapter: the default renderer path remains direct data/static data/custom `ConstellationDataSource`, and accelerated atlas/texture-array rendering requires URLs supplied by the host application.
