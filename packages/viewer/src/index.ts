import { createFlyControls, type FlyControls } from './controls';
import { runtimeAssetsToData } from './data-source';
import { computeLayout } from './layout';
import { PointLodManager } from './lod';
import { createSceneHost, type SceneHost } from './scene';
import { SpriteManager } from './sprites';
import type {
  ConstellationData,
  ConstellationDataSource,
  ConstellationViewer,
  ConstellationViewerOptions,
  PositionedImage,
} from './types';

export type {
  ConstellationData,
  ConstellationDataSource,
  ConstellationImage,
  ConstellationViewer,
  ConstellationViewerOptions,
  ControlsOptions,
  IndexStatus,
  LayoutOptions,
  NearbyQuery,
  PositionedImage,
  RuntimeAsset,
  RuntimeAssetMetadata,
  RuntimeMediaType,
  SpriteOptions,
  Vec3,
} from './types';
export {
  createFetchDataSource,
  createStaticDataSource,
  createSyntheticRuntimeAssets,
  imageToRuntimeAsset,
  runtimeAssetsToData,
} from './data-source';
export { computeLayout, relaxCollisions } from './layout';

interface RenderManager {
  setImages(images: PositionedImage[]): void;
  update(camera: SceneHost['camera'], deltaSeconds: number): void;
  setSelected(id: string | null): void;
  dispose(): void;
}

class ConstellationViewerImpl implements ConstellationViewer {
  readonly container: HTMLElement;
  data: ConstellationData;
  positions: PositionedImage[];

  private readonly sceneHost: SceneHost;
  private readonly controls: FlyControls;
  private sprites: RenderManager;
  private usingLod: boolean;
  private animationFrame: number | null = null;
  private destroyed = false;

  constructor(
    container: HTMLElement,
    data: ConstellationData,
    private readonly options: ConstellationViewerOptions = {},
  ) {
    validateData(data);
    if (container.clientWidth === 0 || container.clientHeight === 0) {
      container.style.minHeight ||= '480px';
    }

    this.container = container;
    this.data = data;
    this.positions = computeLayout(data, options.layout);
    this.sceneHost = createSceneHost(container, options);
    this.sceneHost.scene.userData.camera = this.sceneHost.camera;
    this.controls = createFlyControls(this.sceneHost.camera, this.sceneHost.renderer.domElement, options.controls);
    this.usingLod = shouldUseLod(this.positions.length, options);
    this.sprites = this.createRenderManager(this.positions, this.usingLod);

    this.animate = this.animate.bind(this);
    this.animationFrame = requestAnimationFrame(this.animate);
    options.onReady?.(this);
  }

  focus(): void {
    this.sceneHost.renderer.domElement.focus();
    this.controls.lock();
  }

  setData(data: ConstellationData): void {
    validateData(data);
    this.data = data;
    this.positions = computeLayout(data, this.options.layout);
    const nextUsingLod = shouldUseLod(this.positions.length, this.options);
    if (nextUsingLod !== this.usingLod) {
      this.sprites.dispose();
      this.usingLod = nextUsingLod;
      this.sprites = this.createRenderManager(this.positions, this.usingLod);
    } else {
      this.sprites.setImages(this.positions);
    }
  }

  setSelected(id: string | null): void {
    this.sprites.setSelected(id);
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    if (this.animationFrame !== null) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    this.sprites.dispose();
    this.controls.destroy();
    this.sceneHost.destroy();
  }

  private animate(): void {
    if (this.destroyed) return;
    const delta = Math.min(this.sceneHost.clock.getDelta(), 0.1);
    this.controls.update(delta);
    this.sprites.update(this.sceneHost.camera, delta);
    this.sceneHost.renderer.render(this.sceneHost.scene, this.sceneHost.camera);
    this.animationFrame = requestAnimationFrame(this.animate);
  }

  private createRenderManager(images: PositionedImage[], useLod: boolean): RenderManager {
    const managerOptions = {
      ...(this.options.sprites ?? {}),
      onSelect: this.options.onSelect,
      onHover: this.options.onHover,
    };
    return useLod
      ? new PointLodManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions)
      : new SpriteManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions);
  }
}

export function mount(
  container: HTMLElement,
  data: ConstellationData,
  options: ConstellationViewerOptions = {},
): ConstellationViewer {
  return new ConstellationViewerImpl(container, data, options);
}

export function createViewer(
  container: HTMLElement,
  data: ConstellationData,
  options: ConstellationViewerOptions = {},
): ConstellationViewer {
  return mount(container, data, options);
}

export async function mountFromDataSource(
  container: HTMLElement,
  dataSource: ConstellationDataSource,
  options: ConstellationViewerOptions = {},
): Promise<ConstellationViewer> {
  const assets = await dataSource.getInitialAssets();
  return mount(container, runtimeAssetsToData(assets), options);
}

export async function createViewerFromDataSource(
  container: HTMLElement,
  dataSource: ConstellationDataSource,
  options: ConstellationViewerOptions = {},
): Promise<ConstellationViewer> {
  return mountFromDataSource(container, dataSource, options);
}

function shouldUseLod(count: number, options: ConstellationViewerOptions): boolean {
  const mode = options.sprites?.renderMode ?? 'auto';
  if (mode === 'lod') return true;
  if (mode === 'cards') return false;
  return count >= (options.sprites?.lodThreshold ?? 2_000);
}

function validateData(data: ConstellationData): void {
  const ids = new Set<string>();
  for (const [index, image] of data.images.entries()) {
    if (typeof image.id !== 'string' || image.id.length === 0) {
      throw new Error(`Image at index ${index} must have a non-empty string id.`);
    }
    if (ids.has(image.id)) {
      throw new Error(`Duplicate image id: ${image.id}`);
    }
    ids.add(image.id);
    const hasTextureUrl = typeof image.url === 'string' && image.url.length > 0;
    const hasThumbnailUrl = typeof image.thumbnailUrl === 'string' && image.thumbnailUrl.length > 0;
    if (!hasTextureUrl && !hasThumbnailUrl) {
      throw new Error(`Image ${image.id} must have a non-empty url or thumbnailUrl.`);
    }
  }
}
