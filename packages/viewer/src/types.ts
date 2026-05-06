import type { WebGLRendererParameters } from 'three';

export type Vec3 = readonly [number, number, number];

export interface ConstellationImage {
  /** Stable application-level identifier. */
  id: string;
  /** Image URL used as a fallback for texture loading and full-size display. */
  url: string;
  /** Smaller image URL preferred for in-scene texture loading. */
  thumbnailUrl?: string;
  /** Optional full-resolution image URL for consumers' click/selection UIs. */
  fullUrl?: string;
  /** High-dimensional embedding. Used when `position` is not supplied. */
  embedding?: readonly number[];
  /** Precomputed 3D layout position. Skips browser-side UMAP when supplied. */
  position?: Vec3;
  width?: number;
  height?: number;
  metadata?: Record<string, unknown>;
}

export interface ConstellationData {
  images: ConstellationImage[];
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
  /** Base sprite height in world units. Width preserves image aspect ratio when available. */
  size?: number;
  minSize?: number;
  maxAspectRatio?: number;
  lazyLoadDistance?: number;
  maxConcurrentLoads?: number;
  maxLoadedTextures?: number;
  /** Maximum fraction of viewport height a sprite may occupy before it shrinks. Set to Infinity to disable. */
  maxViewportHeight?: number;
  billboard?: boolean;
  placeholderColor?: number;
  selectedColor?: number;
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

export interface ConstellationViewer {
  readonly container: HTMLElement;
  readonly data: ConstellationData;
  readonly positions: PositionedImage[];
  destroy(): void;
  focus(): void;
  setData(data: ConstellationData): void;
  setSelected(id: string | null): void;
}
