import type { WebGLRendererParameters } from 'three';

export type Vec3 = readonly [number, number, number];

export type RuntimeMediaType = 'image' | 'video' | 'livePhoto' | 'unknown';

export interface RuntimeAssetMetadata {
  sourcePath?: string;
  creationDate?: string;
  mediaType?: RuntimeMediaType;
  favorite?: boolean;
  albumIds?: string[];
  [key: string]: unknown;
}

/**
 * Runtime asset contract for frontend/viewer consumption.
 * Embeddings are intentionally excluded: product/runtime viewing uses
 * precomputed positions from an indexer/backend or a mock data source.
 */
export interface RuntimeAsset {
  id: string;
  thumbnailUrl: string;
  fullUrl?: string;
  width?: number;
  height?: number;
  position: Vec3;
  metadata?: RuntimeAssetMetadata;
}

export interface ConstellationImage {
  /** Stable application-level identifier. */
  id: string;
  /** Legacy/full image URL used as a fallback for texture loading and full-size display. */
  url?: string;
  /** Smaller image URL preferred for in-scene texture loading. Required for RuntimeAsset. */
  thumbnailUrl?: string;
  /** Optional full-resolution image URL for consumers' click/selection UIs. */
  fullUrl?: string;
  /** High-dimensional embedding. Used only as a demo/developer fallback when `position` is not supplied. */
  embedding?: readonly number[];
  /** Precomputed 3D layout position. Preferred runtime path. */
  position?: Vec3;
  width?: number;
  height?: number;
  metadata?: RuntimeAssetMetadata;
}

export interface ConstellationData {
  images: ConstellationImage[];
}

export interface IndexStatus {
  state: 'idle' | 'importing' | 'indexing' | 'ready' | 'error' | string;
  totalAssets?: number;
  indexedAssets?: number;
  message?: string;
  updatedAt?: string;
}

export interface NearbyQuery {
  x: number;
  y: number;
  z: number;
  radius: number;
  limit?: number;
}

export interface ConstellationDataSource {
  getStatus(): Promise<IndexStatus>;
  getInitialAssets(): Promise<RuntimeAsset[]>;
  getNearbyAssets?(query: NearbyQuery): Promise<RuntimeAsset[]>;
  getAsset?(id: string): Promise<RuntimeAsset | null>;
}

export interface LayoutOptions {
  /** Overall multiplier applied to normalized UMAP/precomputed coordinates. */
  scale?: number;
  /** UMAP neighbors. Automatically clamped for small datasets. */
  nNeighbors?: number;
  minDist?: number;
  spread?: number;
  /** Deterministic seed used by UMAP's PRNG. */
  seed?: number;
  /** Center resulting coordinates around the origin. */
  center?: boolean;
  /** Separate nearby UMAP points after layout to reduce sprite overlap while preserving cluster anchors. Defaults to true for embedding-derived layouts. */
  collisionRelaxation?: boolean;
  /** Target minimum world-space distance between relaxed points. Defaults to 10. */
  collisionDistance?: number;
  /** Number of relaxation iterations. Defaults to 35. */
  collisionIterations?: number;
  /** Pull toward original UMAP position each iteration. Higher preserves UMAP more, lower separates more. Defaults to 0.025. */
  collisionAnchorStrength?: number;
}

export interface ControlsOptions {
  enabled?: boolean;
  clickToLock?: boolean;
  moveSpeed?: number;
  sprintMultiplier?: number;
  verticalSpeed?: number;
}

export interface SpriteOptions {
  /** `cards` preserves the legacy mesh-per-image path. `lod` renders all assets as cheap points and promotes nearby/selected assets to textured cards. */
  renderMode?: 'cards' | 'lod' | 'auto';
  /** Dataset size at which `renderMode: "auto"` switches from cards to LOD. */
  lodThreshold?: number;
  /** Base sprite/card height in world units. Width preserves image aspect ratio when available. */
  size?: number;
  minSize?: number;
  maxAspectRatio?: number;
  lazyLoadDistance?: number;
  maxConcurrentLoads?: number;
  maxLoadedTextures?: number;
  /** LOD mode cap for concurrently materialized textured card meshes. Defaults to `maxLoadedTextures` or 400. */
  maxTexturedCards?: number;
  /** Distance after which non-selected LOD cards are evicted. Defaults to 1.35x `lazyLoadDistance`. */
  textureUnloadDistance?: number;
  /** LOD point-cloud marker size in CSS pixels. */
  pointSize?: number;
  pointColor?: number;
  pointOpacity?: number;
  /** World-space raycast radius for LOD point picking. */
  pointPickRadius?: number;
  /** Maximum fraction of viewport height a sprite may occupy before it shrinks. Set to Infinity to disable. */
  maxViewportHeight?: number;
  billboard?: boolean;
  placeholderColor?: number;
  selectedColor?: number;
  /** Use WebGL2 texture-array pages in LOD mode when supported. */
  textureArray?: boolean;
  textureArrayIndexUrl?: string;
  textureArrayPageConcurrency?: number;
  textureArrayMaxPages?: number;
  /** Use server-generated thumbnail atlas pages in LOD mode. */
  atlas?: boolean;
  atlasIndexUrl?: string;
  atlasPageConcurrency?: number;
  atlasMaxPages?: number;
}

export interface ConstellationViewerOptions {
  layout?: LayoutOptions;
  controls?: ControlsOptions;
  sprites?: SpriteOptions;
  backgroundColor?: number;
  camera?: {
    fov?: number;
    near?: number;
    far?: number;
    position?: Vec3;
  };
  renderer?: WebGLRendererParameters;
  onSelect?: (image: ConstellationImage) => void;
  onHover?: (image: ConstellationImage | null) => void;
  onReady?: (viewer: ConstellationViewer) => void;
}

export interface PositionedImage extends ConstellationImage {
  position: Vec3;
}

export interface TextureQueueDebugStats {
  activeLoads: number;
  queued: number;
  loading: number;
  loaded: number;
  totalRequests: number;
  totalLoads: number;
  totalErrors: number;
}

export interface ViewerDebugStats {
  mode: 'cards' | 'lod';
  imageCount: number;
  cameraPosition: Vec3;
  lod?: {
    activeCards: number;
    loadedCards: number;
    capacity: number;
    candidateCount: number;
    nearestUnloadedDistance: number | null;
    lazyLoadDistance: number;
    textureUnloadDistance: number;
    maxTexturedCards: number;
    maxLoadedTextures: number;
    lastUpdateMs: number;
    atlasReady?: boolean;
    atlasPagesLoaded?: number;
    textureArrayReady?: boolean;
    textureArrayPagesLoaded?: number;
    textureQueue: TextureQueueDebugStats;
  };
}

export interface ConstellationViewer {
  readonly container: HTMLElement;
  readonly data: ConstellationData;
  readonly positions: PositionedImage[];
  destroy(): void;
  focus(): void;
  fitToContent(): void;
  resetCamera(): void;
  setData(data: ConstellationData): void;
  setSelected(id: string | null): void;
  getDebugStats(): ViewerDebugStats;
}
