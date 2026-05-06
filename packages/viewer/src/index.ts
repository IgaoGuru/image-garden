import { createFlyControls, type FlyControls } from './controls';
import { computeLayout } from './layout';
import { createSceneHost, type SceneHost } from './scene';
import { SpriteManager } from './sprites';
import type {
  ConstellationData,
  ConstellationViewer,
  ConstellationViewerOptions,
  PositionedImage,
} from './types';

export type {
  ConstellationData,
  ConstellationImage,
  ConstellationViewer,
  ConstellationViewerOptions,
  ControlsOptions,
  LayoutOptions,
  PositionedImage,
  SpriteOptions,
  Vec3,
} from './types';
export { computeLayout, relaxCollisions } from './layout';

class ConstellationViewerImpl implements ConstellationViewer {
  readonly container: HTMLElement;
  data: ConstellationData;
  positions: PositionedImage[];

  private readonly sceneHost: SceneHost;
  private readonly controls: FlyControls;
  private readonly sprites: SpriteManager;
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
    this.sprites = new SpriteManager(this.sceneHost.scene, this.sceneHost.renderer.domElement, this.positions, {
      ...(options.sprites ?? {}),
      onSelect: options.onSelect,
      onHover: options.onHover,
    });

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
    this.sprites.setImages(this.positions);
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
    if (typeof image.url !== 'string' || image.url.length === 0) {
      throw new Error(`Image ${image.id} must have a non-empty url.`);
    }
  }
}
