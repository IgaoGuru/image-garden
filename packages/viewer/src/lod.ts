import {
  BufferGeometry,
  DoubleSide,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  PlaneGeometry,
  Points,
  PointsMaterial,
  Raycaster,
  Scene,
  Vector2,
  Vector3,
} from 'three';

import { TextureLoadQueue } from './loader';
import { viewportHeightScaleForCap } from './sprites';
import type { ConstellationImage, PositionedImage, SpriteOptions, ViewerDebugStats } from './types';

interface LodRecord {
  image: PositionedImage;
  pointIndex: number;
  position: Vector3;
  card?: Mesh<PlaneGeometry, MeshBasicMaterial>;
  baseHeight: number;
  loaded: boolean;
  cancelLoad?: () => void;
  lastDistance: number;
}

export interface PointLodManagerOptions extends SpriteOptions {
  onSelect?: (image: ConstellationImage) => void;
  onHover?: (image: ConstellationImage | null) => void;
}

export class PointLodManager {
  private readonly group = new Group();
  private readonly cardGroup = new Group();
  private readonly raycaster = new Raycaster();
  private readonly pointer = new Vector2(0, 0);
  private readonly cameraForward = new Vector3();
  private readonly spriteOffset = new Vector3();
  private readonly records = new Map<string, LodRecord>();
  private readonly recordsByPointIndex: LodRecord[] = [];
  private readonly textureQueue: TextureLoadQueue;
  private readonly domElement: HTMLElement;
  private readonly options: Required<
    Omit<
      SpriteOptions,
      | 'renderMode'
      | 'lodThreshold'
      | 'selectedColor'
      | 'maxTexturedCards'
      | 'textureUnloadDistance'
      | 'pointColor'
      | 'atlas'
      | 'atlasIndexUrl'
      | 'atlasPageConcurrency'
      | 'atlasMaxPages'
    >
  > & {
    selectedColor: number;
    maxTexturedCards: number;
    textureUnloadDistance: number;
    pointColor: number;
  };
  private readonly onSelect?: (image: ConstellationImage) => void;
  private readonly onHover?: (image: ConstellationImage | null) => void;
  private points: Points<BufferGeometry, PointsMaterial> | null = null;
  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private updateAccumulator = 0.25;
  private debugStats: NonNullable<ViewerDebugStats['lod']> | null = null;

  constructor(
    private readonly scene: Scene,
    domElement: HTMLElement,
    images: PositionedImage[],
    options: PointLodManagerOptions = {},
  ) {
    this.domElement = domElement;
    const lazyLoadDistance = options.lazyLoadDistance ?? 180;
    this.options = {
      size: options.size ?? 8,
      minSize: options.minSize ?? 1,
      maxAspectRatio: options.maxAspectRatio ?? 2.5,
      lazyLoadDistance,
      maxConcurrentLoads: options.maxConcurrentLoads ?? 8,
      maxLoadedTextures: options.maxLoadedTextures ?? 1_000,
      maxViewportHeight: options.maxViewportHeight ?? 0.45,
      billboard: options.billboard ?? true,
      placeholderColor: options.placeholderColor ?? 0x777799,
      selectedColor: options.selectedColor ?? 0xffcc66,
      maxTexturedCards: options.maxTexturedCards ?? Math.min(options.maxLoadedTextures ?? 400, 400),
      textureUnloadDistance: options.textureUnloadDistance ?? lazyLoadDistance * 1.35,
      pointSize: options.pointSize ?? 4,
      pointColor: options.pointColor ?? 0x8ea2ff,
      pointOpacity: options.pointOpacity ?? 0.68,
      pointPickRadius: options.pointPickRadius ?? 8,
    };
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.textureQueue = new TextureLoadQueue(this.options.maxConcurrentLoads);
    this.debugStats = this.emptyDebugStats();
    this.raycaster.params.Points = { threshold: this.options.pointPickRadius };
    this.group.add(this.cardGroup);
    this.scene.add(this.group);
    this.setImages(images);

    this.onPointerMove = this.onPointerMove.bind(this);
    this.onClick = this.onClick.bind(this);
    this.domElement.addEventListener('pointermove', this.onPointerMove);
    this.domElement.addEventListener('click', this.onClick);
  }

  setImages(images: PositionedImage[]): void {
    this.clear();

    const positions = new Float32Array(images.length * 3);
    for (const [index, image] of images.entries()) {
      positions[index * 3] = image.position[0];
      positions[index * 3 + 1] = image.position[1];
      positions[index * 3 + 2] = image.position[2];
      const position = new Vector3(image.position[0], image.position[1], image.position[2]);
      const [, baseHeight] = this.getCardDimensions(image);
      const record: LodRecord = { image, pointIndex: index, position, baseHeight, loaded: false, lastDistance: Infinity };
      this.records.set(image.id, record);
      this.recordsByPointIndex[index] = record;
    }

    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
    const material = new PointsMaterial({
      color: this.options.pointColor,
      opacity: this.options.pointOpacity,
      transparent: this.options.pointOpacity < 1,
      size: this.options.pointSize,
      sizeAttenuation: false,
      depthWrite: false,
    });
    this.points = new Points(geometry, material);
    this.group.add(this.points);
  }

  update(camera: PerspectiveCamera, deltaSeconds: number): void {
    this.updateAccumulator += deltaSeconds;
    if (this.options.billboard) {
      for (const record of this.records.values()) {
        record.card?.quaternion.copy(camera.quaternion);
      }
    }
    this.applyViewportHeightCap(camera);

    if (this.updateAccumulator >= 0.25) {
      this.updateAccumulator = 0;
      this.updateCards(camera.position);
      this.updateHover(camera);
    }
  }

  setSelected(id: string | null): void {
    if (this.selectedId === id) return;
    const previous = this.selectedId ? this.records.get(this.selectedId) : undefined;
    if (previous?.card) this.applySelectionTint(previous, false);
    this.selectedId = id;
    const next = id ? this.records.get(id) : undefined;
    if (next) {
      this.ensureCard(next);
      this.requestTexture(next);
      this.applySelectionTint(next, true);
    }
  }

  pick(): PositionedImage | null {
    const cardIntersections = this.raycaster.intersectObjects(this.cardGroup.children, false);
    const cardObject = cardIntersections[0]?.object;
    const cardId = typeof cardObject?.userData.id === 'string' ? cardObject.userData.id : undefined;
    if (cardId) return this.records.get(cardId)?.image ?? null;

    if (!this.points) return null;
    const pointIntersection = this.raycaster.intersectObject(this.points, false)[0];
    const index = pointIntersection?.index;
    if (index === undefined) return null;
    return this.recordsByPointIndex[index]?.image ?? null;
  }

  getDebugStats(): NonNullable<ViewerDebugStats['lod']> {
    const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
    if (!camera) {
      return {
        ...(this.debugStats ?? this.emptyDebugStats()),
        textureQueue: this.textureQueue.getDebugStats(),
      };
    }
    const startedAt = performance.now();
    for (const record of this.records.values()) {
      record.lastDistance = record.position.distanceTo(camera.position);
    }
    return this.debugSnapshot(performance.now() - startedAt);
  }

  dispose(): void {
    this.domElement.removeEventListener('pointermove', this.onPointerMove);
    this.domElement.removeEventListener('click', this.onClick);
    this.clear();
    this.scene.remove(this.group);
    this.textureQueue.dispose();
  }

  private updateCards(cameraPosition: Vector3): void {
    const startedAt = performance.now();
    const records = [...this.records.values()];
    for (const record of records) {
      record.lastDistance = record.position.distanceTo(cameraPosition);
    }

    const desiredCards = records
      .filter((record) => record.lastDistance <= this.options.lazyLoadDistance)
      .sort((a, b) => a.lastDistance - b.lastDistance)
      .slice(0, this.options.maxTexturedCards);
    const desiredIds = new Set(desiredCards.map((record) => record.image.id));

    for (const record of records) {
      if (!record.card || record.image.id === this.selectedId) continue;
      if (!desiredIds.has(record.image.id) || record.lastDistance > this.options.textureUnloadDistance) {
        this.removeCard(record);
      }
    }

    const capacity = Math.max(
      0,
      this.options.maxTexturedCards - records.filter((record) => record.card).length,
    );
    const candidates = desiredCards
      .filter((record) => !record.card)
      .slice(0, capacity);

    for (const record of candidates) {
      this.ensureCard(record);
      this.requestTexture(record);
    }
    this.debugStats = this.debugSnapshot(performance.now() - startedAt);
  }

  private debugSnapshot(lastUpdateMs: number): NonNullable<ViewerDebugStats['lod']> {
    const records = [...this.records.values()];
    const activeCards = records.filter((record) => record.card).length;
    const loadedCards = records.filter((record) => record.loaded).length;
    const capacity = Math.max(0, this.options.maxTexturedCards - activeCards);
    const unloaded = records.filter((record) => !record.card);
    const nearestUnloadedDistance = unloaded.reduce<number | null>(
      (nearest, record) => (nearest === null ? record.lastDistance : Math.min(nearest, record.lastDistance)),
      null,
    );
    const candidateCount = unloaded.filter((record) => record.lastDistance <= this.options.lazyLoadDistance).length;
    return {
      activeCards,
      loadedCards,
      capacity,
      candidateCount,
      nearestUnloadedDistance,
      lazyLoadDistance: this.options.lazyLoadDistance,
      textureUnloadDistance: this.options.textureUnloadDistance,
      maxTexturedCards: this.options.maxTexturedCards,
      maxLoadedTextures: this.options.maxLoadedTextures,
      lastUpdateMs,
      textureQueue: this.textureQueue.getDebugStats(),
    };
  }

  private emptyDebugStats(): NonNullable<ViewerDebugStats['lod']> {
    return {
      activeCards: 0,
      loadedCards: 0,
      capacity: this.options.maxTexturedCards,
      candidateCount: 0,
      nearestUnloadedDistance: null,
      lazyLoadDistance: this.options.lazyLoadDistance,
      textureUnloadDistance: this.options.textureUnloadDistance,
      maxTexturedCards: this.options.maxTexturedCards,
      maxLoadedTextures: this.options.maxLoadedTextures,
      lastUpdateMs: 0,
      textureQueue: this.textureQueue.getDebugStats(),
    };
  }

  private ensureCard(record: LodRecord): void {
    if (record.card) return;
    const [width, height] = this.getCardDimensions(record.image);
    const geometry = new PlaneGeometry(width, height);
    const material = new MeshBasicMaterial({
      color: record.image.id === this.selectedId ? this.options.selectedColor : this.options.placeholderColor,
      opacity: 0.55,
      transparent: true,
      side: DoubleSide,
      depthWrite: false,
    });
    const card = new Mesh(geometry, material);
    card.position.copy(record.position);
    card.userData = { id: record.image.id };
    record.card = card;
    record.baseHeight = height;
    record.loaded = false;
    this.cardGroup.add(card);
  }

  private requestTexture(record: LodRecord): void {
    if (!record.card || record.loaded || this.textureQueue.has(record.image.id)) return;
    const url = record.image.thumbnailUrl ?? record.image.url;
    if (!url) return;
    record.cancelLoad = this.textureQueue.request({
      id: record.image.id,
      url,
      onLoad: (texture) => {
        if (!record.card) {
          this.textureQueue.disposeTexture(record.image.id);
          return;
        }
        record.card.material.map = texture;
        record.card.material.color.setHex(record.image.id === this.selectedId ? this.options.selectedColor : 0xffffff);
        record.card.material.opacity = 1;
        record.card.material.needsUpdate = true;
        record.loaded = true;
        record.cancelLoad = undefined;
      },
      onError: () => {
        if (record.card) {
          record.card.material.color.setHex(0x663333);
          record.card.material.opacity = 0.8;
        }
        record.cancelLoad = undefined;
      },
    });
  }

  private applyViewportHeightCap(camera: PerspectiveCamera): void {
    if (!Number.isFinite(this.options.maxViewportHeight)) return;
    camera.updateMatrixWorld(true);
    camera.getWorldDirection(this.cameraForward).normalize();
    for (const record of this.records.values()) {
      if (!record.card) continue;
      this.spriteOffset.subVectors(record.card.position, camera.position);
      const depth = this.spriteOffset.dot(this.cameraForward);
      const scale = viewportHeightScaleForCap({
        spriteWorldHeight: record.baseHeight,
        depth,
        cameraFovDegrees: camera.fov,
        maxViewportHeight: this.options.maxViewportHeight,
      });
      record.card.scale.setScalar(scale);
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
    if (!this.onSelect) return;
    const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
    if (!camera) return;
    this.raycaster.setFromCamera(document.pointerLockElement === this.domElement ? new Vector2(0, 0) : this.pointer, camera);
    const image = this.pick();
    if (image) {
      this.setSelected(image.id);
      this.onSelect(image);
    }
  }

  private clear(): void {
    for (const record of this.records.values()) {
      this.removeCard(record);
    }
    if (this.points) {
      this.points.geometry.dispose();
      this.points.material.dispose();
      this.group.remove(this.points);
      this.points = null;
    }
    this.records.clear();
    this.recordsByPointIndex.length = 0;
    this.selectedId = null;
    this.hoveredId = null;
  }

  private removeCard(record: LodRecord): void {
    record.cancelLoad?.();
    record.cancelLoad = undefined;
    this.textureQueue.disposeTexture(record.image.id);
    if (!record.card) return;
    record.card.geometry.dispose();
    record.card.material.dispose();
    this.cardGroup.remove(record.card);
    record.card = undefined;
    record.loaded = false;
  }

  private applySelectionTint(record: LodRecord, selected: boolean): void {
    record.card?.material.color.setHex(selected ? this.options.selectedColor : record.loaded ? 0xffffff : this.options.placeholderColor);
  }

  private getCardDimensions(image: PositionedImage): [number, number] {
    const height = Math.max(this.options.minSize, this.options.size);
    const rawAspect = image.width && image.height ? image.width / image.height : 1;
    const aspect = Math.min(this.options.maxAspectRatio, Math.max(1 / this.options.maxAspectRatio, rawAspect));
    return [height * aspect, height];
  }
}
