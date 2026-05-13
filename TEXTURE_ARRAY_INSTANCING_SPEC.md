# Texture Array + Instancing Rendering Spec

Branch/worktree: `texture-array-instancing-spec`

Goal: keep the atlas branch's performance shape while fixing atlas-specific quality problems: color mismatch, padding/black borders, aspect surprises, and low close-up resolution.

## Summary

Use three rendering tiers:

1. **Far tier:** point cloud for all assets.
2. **Mid tier:** low/medium texture array pages rendered with instanced quads.
3. **Near/selected tier:** higher-resolution texture array pages or individual full image only for inspection.

Texture arrays replace texture atlases for the main thumbnail-card tier.

```text
Current atlas: one 4096x4096 JPEG sheet, each image = UV rect
Proposed array: one GPU DataTexture2DArray page, each image = layer index
```

Each visible card is still one instance. Per instance data:

```text
matrix / position / scale
layer index
aspect ratio / display size
optional opacity/tint
```

Shader samples:

```glsl
texture(thumbnailArray, vec3(uv, layer));
```

## Why Texture Arrays

Texture atlases are fast but create problems:

- fixed square cells cause padding unless UV rects are exact
- bilinear filtering can bleed neighboring cells
- JPEG atlas re-encode changes colors/quality
- one atlas resolution is too low for near thumbnails or too big for far thumbnails
- aspect ratio logic has to coordinate card geometry and cell rects

Texture arrays improve this:

- each thumbnail has full `[0,1]` UVs in its own layer
- no neighbor-cell bleeding
- no black padding needed if thumbnails are generated/cropped consistently
- layer index is just an instanced attribute
- still few draw calls: one draw call per array page/tier

## Requirements

### Performance targets

Measured by `pnpm bench:runtime`:

- 8k visible cards loaded: <= current atlas baseline (~4.7s warm on 8751 images)
- individual thumbnail HTTP requests: 0 in texture-array path
- draw calls: O(number of array pages), not O(number of images)
- no user-visible hitch during initial load or flight

### Quality targets

- no black borders/padding from packing
- no distorted aspect ratios
- colors match current non-atlas thumbnail cards as closely as possible
- near thumbnails should support >=256px, ideally 384/512px tier

### Compatibility

- WebGL2 required for `sampler2DArray` / `DataTexture2DArray`
- fallback to atlas or current individual-card LOD if WebGL2/texture-array unsupported

## Architecture

### Backend API

Add texture-array manifest endpoints. Do **not** try to send GPU-native texture arrays directly; send page manifests + binary/image data that browser can assemble.

Option A: server sends zip/binary RGBA page:

```http
GET /api/texture-array/index.json
GET /api/texture-array/pages/tier-128/page-0.rgba
```

Manifest:

```json
{
  "format": "rgba8",
  "tiers": [
    {
      "name": "thumb128",
      "width": 128,
      "height": 128,
      "layersPerPage": 512,
      "pages": [
        { "index": 0, "url": "/api/texture-array/pages/thumb128/page-0.rgba", "layers": 512 },
        { "index": 1, "url": "/api/texture-array/pages/thumb128/page-1.rgba", "layers": 512 }
      ]
    }
  ],
  "entries": [
    { "id": "...", "tier": "thumb128", "page": 0, "layer": 42, "width": 1536, "height": 2048 }
  ]
}
```

Pros:

- exact pixels, no JPEG color surprises
- direct upload into `DataTexture2DArray`

Cons:

- big transfer size unless compressed externally
- 128x128 RGBA x 512 ~= 32MB/page uncompressed

Option B: server sends lossless/lossy image strips, client decodes and copies into array:

```http
GET /api/texture-array/pages/thumb128/page-0.webp
```

Page image layout could be grid/strip internally, but client copies each tile into array layers. This uses browser image decode but avoids atlas sampling artifacts after upload.

Pros:

- smaller network/disk
- browser decodes compressed images

Cons:

- still need copy tiles into typed array/canvas
- more client CPU

Recommendation for first prototype: **Option B with PNG/WebP/JPEG grid pages**, client copies into `DataTexture2DArray`. It is closest to current atlas implementation and easiest to compare.

### Texture array page object

Frontend representation:

```ts
interface TextureArrayPage {
  tier: string;
  page: number;
  texture: DataTexture2DArray;
  mesh: InstancedMesh<PlaneGeometry, ShaderMaterial>;
  capacity: number;
  visibleIds: Set<string>;
}
```

### Shader

Vertex attributes:

```glsl
attribute float instanceLayer;
```

Fragment shader:

```glsl
uniform sampler2DArray mapArray;
varying vec2 vUv;
varying float vLayer;

void main() {
  vec4 color = texture(mapArray, vec3(vUv, vLayer));
  gl_FragColor = color;
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
```

Important:

- use Three color-management chunks
- mark `DataTexture2DArray.colorSpace = SRGBColorSpace` if source pixels are sRGB and Three supports it correctly
- compare against `MeshBasicMaterial` individual thumbnail baseline

### LOD selection

Use same point-cloud + throttled selector pattern, but split by tier.

```text
Every 100-250ms:
  compute candidate distances (squared)
  choose nearest K for 128/256/512 tiers
  request needed pages by priority
  rebuild instance buffers only for changed pages
```

Suggested tiers:

| Tier | Resolution | Count budget | Use |
|---|---:|---:|---|
| far | points | 30k | all assets |
| mid | 128 | 8k-12k | broad visible field |
| near | 384/512 | 256-1000 | close cards |
| selected | full | 1-4 | clicked/inspected |

Initial prototype can implement only one 256px texture-array tier, then add 128/512.

### Page sizing

Practical WebGL2 constraints:

- `MAX_ARRAY_TEXTURE_LAYERS` varies by GPU/browser.
- Query at runtime:

```ts
const maxLayers = gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS);
const maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE);
```

Safe defaults:

```text
layersPerPage: 256 or 512
thumb128 page: 128 x 128 x 512 layers
thumb256 page: 256 x 256 x 256 layers
thumb512 page: 512 x 512 x 64/128 layers
```

Do not assume 9000 layers in one array.

### Upload budget

Avoid uploading too much texture data in one frame.

```text
max page uploads per frame: 1
max decoded pending pages: 2-4
max active page fetches: 2-6
```

Large page uploads can cause hitches. Benchmark `p95/p99 frame` later if added.

## Backend generation

Texture-array tiers should use existing sanitized thumbnails/images.

For each asset:

1. open thumbnail or canonical image
2. resize to tier dimensions
3. choose fit mode:
   - `contain`: preserve whole image, transparent/neutral padding
   - `cover`: crop to fill fixed array layer
4. write page image/cache
5. manifest records original aspect ratio

For cards, use original aspect ratio in geometry. The texture can be `cover` to avoid borders, or `contain` with transparent padding. Preferred:

- mid/far: `cover` for no borders, acceptable crop
- near: `contain` or exact aspect-ratio layer later

Alternative for exact aspect without padding:

- use non-square layers impossible within one texture array; arrays require same dimensions per layer
- keep aspect by card geometry and use `cover` crop inside fixed layer

## Frontend implementation plan

### Phase 1: Prototype single-tier texture array

- Add `TextureArrayLodManager` beside `AtlasLodManager`.
- Use `DataTexture2DArray` pages.
- Use same instanced quad approach.
- Fallback to atlas if unsupported.
- Add debug counters:
  - `textureArrayReady`
  - `textureArrayPagesLoaded`
  - `layersVisible`
  - `arrayUploadMs`

### Phase 2: Backend manifest/page cache

- Add `/api/texture-array/index.json`
- Add `/api/texture-array/pages/<tier>/page-<n>.<format>`
- Cache generated pages under:

```text
assets/texture-array/thumb256/page-0.webp
assets/texture-array/thumb256/page-0.manifest.json
```

### Phase 3: Multi-tier LOD

- 128 tier for broad field
- 384/512 tier for near cards
- selected individual full image

### Phase 4: Bench/regression gates

Extend `pnpm bench:runtime` to record:

- texture array page requests
- texture array pages loaded
- first array card
- 8k array cards
- individual thumbnail requests should remain 0 for array path

## Risks

1. **Browser support**
   - WebGL2 generally available, but `DataTexture2DArray` support must be tested.
   - Fallback required.

2. **Upload hitches**
   - Large `texImage3D` uploads may pause rendering.
   - Need page upload budget and smaller pages.

3. **Color management**
   - Custom shader still needs exact Three color-space path.
   - Validate against individual `MeshBasicMaterial` thumbnail rendering.

4. **Memory**
   - 256px RGBA x 256 layers ~= 64MB per page before mipmaps.
   - Need LRU page budgets.

5. **Quality tradeoff**
   - Fixed-size texture array layers cannot preserve arbitrary aspect without crop/padding.
   - Use card aspect ratio + cover crop, or multi-aspect buckets later.

## Open Questions

- Should mid-tier use `cover` crop or `contain` padding?
- What default near-tier resolution is acceptable: 256, 384, or 512?
- Do we accept WebGL2-only enhanced path with atlas fallback?
- Are compressed GPU formats (KTX2/Basis) worth introducing later?

## Suggested default target

For first real implementation:

```text
point cloud: all assets
texture array tier: 256px cover-cropped layers
visible budget: 8k
pages: 256 layers/page
page budget: 40 pages max but load by LRU/priority
near selected: individual /api/files or higher-res later
```

This should preserve the atlas branch's main win (instancing + few draw calls) while reducing atlas-specific visual artifacts and giving a clean path to higher-resolution near thumbnails.
