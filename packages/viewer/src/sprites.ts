import {
  DoubleSide,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Raycaster,
  Scene,
  Vector2,
  Vector3,
} from 'three';

import { TextureLoadQueue } from './loader';
import type { ConstellationImage, PositionedImage, SpriteOptions, ViewerDebugStats } from './types';

interface SpriteRecord {
  image: PositionedImage;
  mesh: Mesh<PlaneGeometry, MeshBasicMaterial>;
  baseHeight: number;
  loaded: boolean;
  cancelLoad?: () => void;
}

export interface SpriteManagerOptions extends SpriteOptions {
  onSelect?: (image: ConstellationImage) => void;
  onHover?: (image: ConstellationImage | null) => void;
}

export function viewportHeightScaleForCap(options: {
  spriteWorldHeight: number;
  depth: number;
  cameraFovDegrees: number;
  maxViewportHeight: number;
}): number {
  if (
    options.spriteWorldHeight <= 0 ||
    options.depth <= 0 ||
    options.maxViewportHeight <= 0 ||
    !Number.isFinite(options.maxViewportHeight)
  ) {
    return 1;
  }
  const visibleWorldHeight =
    2 * options.depth * Math.tan((options.cameraFovDegrees * Math.PI) / 360);
  const cappedScale =
    (options.maxViewportHeight * visibleWorldHeight) / options.spriteWorldHeight;
  return Math.min(1, Math.max(0, cappedScale));
}

export class SpriteManager {
  private readonly group = new Group();
  private readonly raycaster = new Raycaster();
  private readonly pointer = new Vector2(0, 0);
  private readonly cameraForward = new Vector3();
  private readonly spriteOffset = new Vector3();
  private readonly records = new Map<string, SpriteRecord>();
  private readonly textureQueue: TextureLoadQueue;
  private readonly domElement: HTMLElement;
  private readonly options: Required<
    Pick<
      SpriteOptions,
      | 'size'
      | 'minSize'
      | 'maxAspectRatio'
      | 'lazyLoadDistance'
      | 'maxConcurrentLoads'
      | 'maxLoadedTextures'
      | 'maxViewportHeight'
      | 'billboard'
      | 'placeholderColor'
      | 'maxSelectionDistance'
    >
  > & { selectedColor: number };
  private readonly onSelect?: (image: ConstellationImage) => void;
  private readonly onHover?: (image: ConstellationImage | null) => void;
  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private loadAccumulator = 0;

  constructor(
    private readonly scene: Scene,
    domElement: HTMLElement,
    images: PositionedImage[],
    options: SpriteManagerOptions = {},
  ) {
    this.domElement = domElement;
    this.options = {
      size: options.size ?? 8,
      minSize: options.minSize ?? 1,
      maxAspectRatio: options.maxAspectRatio ?? 2.5,
      lazyLoadDistance: options.lazyLoadDistance ?? 180,
      maxConcurrentLoads: options.maxConcurrentLoads ?? 8,
      maxLoadedTextures: options.maxLoadedTextures ?? 1_000,
      maxViewportHeight: options.maxViewportHeight ?? 0.45,
      billboard: options.billboard ?? true,
      placeholderColor: options.placeholderColor ?? 0x777799,
      maxSelectionDistance: options.maxSelectionDistance ?? Infinity,
      selectedColor: options.selectedColor ?? 0xffcc66,
    };
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.textureQueue = new TextureLoadQueue(this.options.maxConcurrentLoads);
    this.scene.add(this.group);
    this.setImages(images);

    this.onPointerMove = this.onPointerMove.bind(this);
    this.onClick = this.onClick.bind(this);
    this.domElement.addEventListener('pointermove', this.onPointerMove);
    this.domElement.addEventListener('click', this.onClick);
  }

  setImages(images: PositionedImage[]): void {
    this.clearMeshes();

    for (const image of images) {
      const [width, height] = this.getSpriteDimensions(image);
      const geometry = new PlaneGeometry(width, height);
      const material = new MeshBasicMaterial({
        color: this.options.placeholderColor,
        opacity: 0.55,
        transparent: true,
        side: DoubleSide,
        depthWrite: false,
      });
      const mesh = new Mesh(geometry, material);
      mesh.position.set(image.position[0], image.position[1], image.position[2]);
      mesh.userData = { id: image.id };
      this.group.add(mesh);
      this.records.set(image.id, { image, mesh, baseHeight: height, loaded: false });
    }
  }

  update(camera: PerspectiveCamera, deltaSeconds: number): void {
    if (this.options.billboard) {
      for (const record of this.records.values()) {
        record.mesh.quaternion.copy(camera.quaternion);
      }
    }

    this.applyViewportHeightCap(camera);

    this.loadAccumulator += deltaSeconds;
    if (this.loadAccumulator >= 0.25) {
      this.loadAccumulator = 0;
      this.loadNearby(camera.position);
      this.updateHover(camera);
    }
  }

  setSelected(id: string | null): void {
    if (this.selectedId === id) return;
    const previous = this.selectedId ? this.records.get(this.selectedId) : undefined;
    if (previous) this.applySelectionTint(previous, false);
    this.selectedId = id;
    const next = id ? this.records.get(id) : undefined;
    if (next) this.applySelectionTint(next, true);
  }

  pick(): PositionedImage | null {
    const intersections = this.raycaster.intersectObjects([...this.records.values()].map((record) => record.mesh), false);
    if (intersections.length === 0) return null;
    const object = intersections[0]?.object;
    const id = typeof object?.userData.id === 'string' ? object.userData.id : undefined;
    return id ? this.records.get(id)?.image ?? null : null;
  }

  getDebugStats(): NonNullable<ViewerDebugStats['lod']> {
    const records = [...this.records.values()];
    const loadedCount = records.filter((record) => record.loaded).length;
    const unloaded = records.filter((record) => !record.loaded && !this.textureQueue.has(record.image.id));
    const nearestUnloadedDistance = unloaded.reduce<number | null>(
      (nearest, record) => {
        const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
        const distance = camera ? record.mesh.position.distanceTo(camera.position) : Infinity;
        return nearest === null ? distance : Math.min(nearest, distance);
      },
      null,
    );
    return {
      activeCards: records.length,
      loadedCards: loadedCount,
      capacity: Math.max(0, this.options.maxLoadedTextures - loadedCount),
      candidateCount: unloaded.filter((record) => {
        const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
        return camera ? record.mesh.position.distanceTo(camera.position) <= this.options.lazyLoadDistance : false;
      }).length,
      nearestUnloadedDistance,
      lazyLoadDistance: this.options.lazyLoadDistance,
      textureUnloadDistance: Infinity,
      maxTexturedCards: records.length,
      maxLoadedTextures: this.options.maxLoadedTextures,
      lastUpdateMs: 0,
      textureQueue: this.textureQueue.getDebugStats(),
    };
  }

  dispose(): void {
    this.domElement.removeEventListener('pointermove', this.onPointerMove);
    this.domElement.removeEventListener('click', this.onClick);
    this.clearMeshes();
    this.scene.remove(this.group);
    this.textureQueue.dispose();
  }

  private applyViewportHeightCap(camera: PerspectiveCamera): void {
    if (!Number.isFinite(this.options.maxViewportHeight)) return;
    camera.updateMatrixWorld(true);
    camera.getWorldDirection(this.cameraForward).normalize();
    for (const record of this.records.values()) {
      this.spriteOffset.subVectors(record.mesh.position, camera.position);
      const depth = this.spriteOffset.dot(this.cameraForward);
      const scale = viewportHeightScaleForCap({
        spriteWorldHeight: record.baseHeight,
        depth,
        cameraFovDegrees: camera.fov,
        maxViewportHeight: this.options.maxViewportHeight,
      });
      record.mesh.scale.setScalar(scale);
    }
  }

  private loadNearby(cameraPosition: Vector3): void {
    const loadedCount = [...this.records.values()].filter((record) => record.loaded).length;
    if (loadedCount >= this.options.maxLoadedTextures) return;

    const candidates = [...this.records.values()]
      .filter((record) => !record.loaded && !this.textureQueue.has(record.image.id))
      .map((record) => ({
        record,
        distance: record.mesh.position.distanceTo(cameraPosition),
      }))
      .filter(({ distance }) => distance <= this.options.lazyLoadDistance)
      .sort((a, b) => a.distance - b.distance)
      .slice(0, Math.max(0, this.options.maxLoadedTextures - loadedCount));

    for (const { record } of candidates) {
      const url = record.image.thumbnailUrl ?? record.image.url;
      if (!url) continue;
      record.cancelLoad = this.textureQueue.request({
        id: record.image.id,
        url,
        onLoad: (texture) => {
          record.mesh.material.map = texture;
          record.mesh.material.color.setHex(record.image.id === this.selectedId ? this.options.selectedColor : 0xffffff);
          record.mesh.material.opacity = 1;
          record.mesh.material.needsUpdate = true;
          record.loaded = true;
          record.cancelLoad = undefined;
        },
        onError: () => {
          record.mesh.material.color.setHex(0x663333);
          record.mesh.material.opacity = 0.8;
          record.cancelLoad = undefined;
        },
      });
    }
  }

  private updateHover(camera: PerspectiveCamera): void {
    this.raycaster.setFromCamera(this.pointer, camera);
    const image = this.pick();
    const nextId = image?.id ?? null;
    if (nextId === this.hoveredId) return;
    this.hoveredId = nextId;
    this.onHover?.(image);
  }

  private onPointerMove(event: PointerEvent): void {
    if (document.pointerLockElement === this.domElement) {
      this.pointer.set(0, 0);
      return;
    }

    const rect = this.domElement.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / Math.max(rect.width, 1)) * 2 - 1;
    const y = -(((event.clientY - rect.top) / Math.max(rect.height, 1)) * 2 - 1);
    this.pointer.set(x, y);
  }

  private onClick(): void {
    const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
    if (!camera) return;
    this.raycaster.setFromCamera(document.pointerLockElement === this.domElement ? new Vector2(0, 0) : this.pointer, camera);
    const image = this.pick();
    if (!image) return;
    if (image.id === this.selectedId) {
      this.setSelected(null);
      return;
    }
    const record = this.records.get(image.id);
    if (!record || record.mesh.position.distanceTo(camera.position) > this.options.maxSelectionDistance) return;
    this.setSelected(image.id);
    this.onSelect?.(image);
  }

  private clearMeshes(): void {
    for (const record of this.records.values()) {
      record.cancelLoad?.();
      this.textureQueue.disposeTexture(record.image.id);
      record.mesh.geometry.dispose();
      record.mesh.material.dispose();
      this.group.remove(record.mesh);
    }
    this.records.clear();
    this.selectedId = null;
    this.hoveredId = null;
  }

  private applySelectionTint(record: SpriteRecord, selected: boolean): void {
    record.mesh.material.color.setHex(selected ? this.options.selectedColor : record.loaded ? 0xffffff : this.options.placeholderColor);
  }

  private getSpriteDimensions(image: PositionedImage): [number, number] {
    const height = Math.max(this.options.minSize, this.options.size);
    const rawAspect = image.width && image.height ? image.width / image.height : 1;
    const aspect = Math.min(this.options.maxAspectRatio, Math.max(1 / this.options.maxAspectRatio, rawAspect));
    return [height * aspect, height];
  }
}
