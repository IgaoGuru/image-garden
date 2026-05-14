import { Vector3 } from 'three';

import { AtlasLodManager } from './atlas-lod';
import { TextureArrayLodManager } from './texture-array-lod';
import { createFlyControls, type FlyControls } from './controls';
import { runtimeAssetsToData } from './data-source';
import { computeLayout } from './layout';
import { PointLodManager } from './lod';
import { createSceneHost, type SceneHost } from './scene';
import { SpriteManager } from './sprites';
import type {
  ConstellationData,
  ConstellationDataSource,
  ImageGardenViewer,
  ImageGardenViewerOptions,
  PositionedImage,
  ViewerDebugStats,
} from './types';

export type {
  ConstellationData,
  ConstellationDataSource,
  ConstellationImage,
  ConstellationViewer,
  ConstellationViewerOptions,
  ImageGardenViewer,
  ImageGardenViewerOptions,
  ControlsOptions,
  IndexStatus,
  LayoutOptions,
  NearbyQuery,
  PositionedImage,
  RuntimeAsset,
  RuntimeAssetMetadata,
  RuntimeMediaType,
  SpriteOptions,
  TextureQueueDebugStats,
  Vec3,
  ViewerDebugStats,
} from './types';
export type { FetchDataSourceEndpoints, FetchDataSourceOptions, StaticDataSourceOptions, SyntheticAssetOptions } from './data-source';
export {
  STUDIO_API_ENDPOINTS,
  createFetchDataSource,
  createStaticDataSource,
  createStudioDataSource,
  createSyntheticRuntimeAssets,
  imageToRuntimeAsset,
  runtimeAssetsToData,
} from './data-source';
export { computeLayout, relaxCollisions } from './layout';

interface RenderManager {
  setImages(images: PositionedImage[]): void;
  update(camera: SceneHost['camera'], deltaSeconds: number): void;
  setSelected(id: string | null): void;
  getDebugStats(): NonNullable<ViewerDebugStats['lod']>;
  dispose(): void;
}

class ImageGardenViewerImpl implements ImageGardenViewer {
  readonly container: HTMLElement;
  data: ConstellationData;
  positions: PositionedImage[];

  private readonly sceneHost: SceneHost;
  private readonly controls: FlyControls;
  private sprites: RenderManager;
  private usingLod: boolean;
  private readonly initialCameraPosition: PositionedImage['position'];
  private animationFrame: number | null = null;
  private destroyed = false;

  constructor(
    container: HTMLElement,
    data: ConstellationData,
    private readonly options: ImageGardenViewerOptions = {},
  ) {
    validateData(data);
    if (container.clientWidth === 0 || container.clientHeight === 0) {
      container.style.minHeight ||= '480px';
    }

    this.container = container;
    this.data = data;
    this.positions = computeLayout(data, options.layout);
    this.sceneHost = createSceneHost(container, options);
    this.initialCameraPosition = [
      this.sceneHost.camera.position.x,
      this.sceneHost.camera.position.y,
      this.sceneHost.camera.position.z,
    ];
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

  fitToContent(): void {
    this.controls.unlock();
    const radius = contentRadius(this.positions);
    const distance = Math.max(180, radius * 2.2);
    this.sceneHost.camera.position.set(0, 0, distance);
    this.sceneHost.camera.lookAt(0, 0, 0);
  }

  resetCamera(): void {
    this.controls.unlock();
    this.sceneHost.camera.position.set(
      this.initialCameraPosition[0],
      this.initialCameraPosition[1],
      this.initialCameraPosition[2],
    );
    this.sceneHost.camera.lookAt(0, 0, 0);
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

  getDebugStats(): ViewerDebugStats {
    return {
      mode: this.usingLod ? 'lod' : 'cards',
      imageCount: this.positions.length,
      cameraPosition: [
        this.sceneHost.camera.position.x,
        this.sceneHost.camera.position.y,
        this.sceneHost.camera.position.z,
      ],
      lod: this.sprites.getDebugStats(),
    };
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
    if (!useLod) {
      return new SpriteManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions);
    }
    if (
      this.options.sprites?.textureArray === true
      && this.options.sprites.textureArrayIndexUrl
      && this.sceneHost.renderer.capabilities.isWebGL2
    ) {
      return new TextureArrayLodManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions);
    }
    if (this.options.sprites?.atlas === true && this.options.sprites.atlasIndexUrl) {
      return new AtlasLodManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions);
    }
    return new PointLodManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, images, managerOptions);
  }
}

export function mount(
  container: HTMLElement,
  data: ConstellationData,
  options: ImageGardenViewerOptions = {},
): ImageGardenViewer {
  return new ImageGardenViewerImpl(container, data, options);
}

export function createViewer(
  container: HTMLElement,
  data: ConstellationData,
  options: ImageGardenViewerOptions = {},
): ImageGardenViewer {
  return mount(container, data, options);
}

export async function mountFromDataSource(
  container: HTMLElement,
  dataSource: ConstellationDataSource,
  options: ImageGardenViewerOptions = {},
): Promise<ImageGardenViewer> {
  const assets = await dataSource.getInitialAssets();
  return mount(container, runtimeAssetsToData(assets), options);
}

export async function createViewerFromDataSource(
  container: HTMLElement,
  dataSource: ConstellationDataSource,
  options: ImageGardenViewerOptions = {},
): Promise<ImageGardenViewer> {
  return mountFromDataSource(container, dataSource, options);
}

function contentRadius(images: PositionedImage[]): number {
  if (images.length === 0) return 120;
  let radius = 0;
  for (const image of images) {
    radius = Math.max(radius, new Vector3(...image.position).length());
  }
  return radius;
}

function shouldUseLod(count: number, options: ImageGardenViewerOptions): boolean {
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
