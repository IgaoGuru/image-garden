# Constellation structure

Constellation is local-first photo mapping.

```text
photos
  → studio
  → playview
  → viewer
```

## studio

Backend engine.

Owns:

- source import
- image normalization
- embedding generation/cache
- layout generation
- SQLite catalog
- local HTTP API
- static file serving

Does not own consumer UI.

Language: Python.

## viewer

Renderer library.

Owns:

- Three.js scene
- camera and controls
- sprites/cards/LOD
- rendering positioned assets

Does not own import, onboarding, database, jobs, or filesystem access.

Language: TypeScript.

## playview

Consumer app shell.

Owns:

- onboarding
- progress UI
- tutorial
- Esc menu
- calls to studio API
- mounting viewer

Does not own embedding, layout, indexing, or storage.

Language: TypeScript.

## scripts

Repo operations.

Owns:

- install
- release bundle
- app data cleanup
- tool orchestration

Languages: Bash, PowerShell, Node.js.

## Ontology

```text
Source
  raw place photos come from

Asset
  normalized local image owned by app

Embedding
  semantic vector made by studio

Position
  3D coordinate made by layout

Runtime asset
  positioned thing playview gives viewer

Catalog
  SQLite record of known assets

Playview
  user-facing shell

Viewer
  pure renderer
```

Hard rule:

```text
studio does work
playview does app UI
viewer draws
scripts package/run
```
