import {
  BufferGeometry,
  DataArrayTexture,
  DoubleSide,
  DynamicDrawUsage,
  Float32BufferAttribute,
  GLSL3,
  Group,
  InstancedBufferAttribute,
  InstancedMesh,
  LinearFilter,
  Object3D,
  PerspectiveCamera,
  PlaneGeometry,
  Points,
  PointsMaterial,
  Quaternion,
  Raycaster,
  Scene,
  ShaderMaterial,
  RGBAFormat,
  SRGBColorSpace,
  UnsignedByteType,
  Vector2,
  Vector3,
} from 'three';

import { viewportHeightScaleForCap } from './sprites';
import type {
  ConstellationImage,
  PositionedImage,
  SpriteOptions,
  TextureQueueDebugStats,
  ViewerDebugStats,
} from './types';

interface TextureArrayIndexEntry {
  id: string;
  page: number;
  layer: number;
}

interface TextureArrayIndexPage {
  index: number;
  url: string;
  layers: number;
}

interface TextureArrayIndex {
  entries: TextureArrayIndexEntry[];
  pages: TextureArrayIndexPage[];
  thumbSize: number;
  layersPerPage: number;
  cols: number;
}

interface TextureArrayRecord {
  image: PositionedImage;
  pointIndex: number;
  position: Vector3;
  textureArray?: TextureArrayIndexEntry;
  lastDistance: number;
  baseHeight: number;
}

interface TextureArrayPageView {
  page: TextureArrayIndexPage;
  texture: DataArrayTexture;
  mesh: InstancedMesh<PlaneGeometry, ShaderMaterial>;
  layer: InstancedBufferAttribute;
  visibleIds: Set<string>;
  lastUsedFrame: number;
}

export interface TextureArrayLodManagerOptions extends SpriteOptions {
  onSelect?: (image: ConstellationImage) => void;
  onHover?: (image: ConstellationImage | null) => void;
}

export class TextureArrayLodManager {
  private readonly group = new Group();
  private readonly cardGroup = new Group();
  private readonly raycaster = new Raycaster();
  private readonly pointer = new Vector2(0, 0);
  private readonly cameraForward = new Vector3();
  private readonly spriteOffset = new Vector3();
  private readonly records = new Map<string, TextureArrayRecord>();
  private readonly recordsByPointIndex: TextureArrayRecord[] = [];
  private readonly pageQueue: number[] = [];
  private readonly pageQueued = new Set<number>();
  private readonly pageLoading = new Set<number>();
  private readonly pageViews = new Map<number, TextureArrayPageView>();
  private readonly pageByIndex = new Map<number, TextureArrayIndexPage>();
  private readonly domElement: HTMLElement;
  private readonly options: Required<
    Omit<
      SpriteOptions,
      | 'renderMode'
      | 'lodThreshold'
      | 'selectedColor'
      | 'textureUnloadDistance'
      | 'pointColor'
      | 'textureArray'
      | 'textureArrayIndexUrl'
      | 'atlas'
      | 'atlasIndexUrl'
      | 'atlasPageConcurrency'
      | 'atlasMaxPages'
      | 'textureArrayPageConcurrency'
      | 'textureArrayMaxPages'
    >
  > & {
    selectedColor: number;
    textureUnloadDistance: number;
    pointColor: number;
    textureArrayIndexUrl: string;
    textureArrayPageConcurrency: number;
    textureArrayMaxPages: number;
  };
  private readonly onSelect?: (image: ConstellationImage) => void;
  private readonly onHover?: (image: ConstellationImage | null) => void;
  private points: Points<BufferGeometry, PointsMaterial> | null = null;
  private textureArrayIndex: TextureArrayIndex | null = null;
  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private updateAccumulator = 0.25;
  private frame = 0;
  private activePageLoads = 0;
  private totalPageRequests = 0;
  private totalPageLoads = 0;
  private totalPageErrors = 0;
  private desiredRecords: TextureArrayRecord[] = [];
  private debugStats: NonNullable<ViewerDebugStats['lod']> | null = null;

  constructor(
    private readonly scene: Scene,
    domElement: HTMLElement,
    images: PositionedImage[],
    options: TextureArrayLodManagerOptions = {},
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
      textureArrayIndexUrl: options.textureArrayIndexUrl ?? '/api/texture-array/index.json?thumbSize=128&layersPerPage=256',
      textureArrayPageConcurrency: options.textureArrayPageConcurrency ?? 4,
      textureArrayMaxPages: options.textureArrayMaxPages ?? 16,
    };
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.debugStats = this.emptyDebugStats();
    this.raycaster.params.Points = { threshold: this.options.pointPickRadius };
    this.group.add(this.cardGroup);
    this.scene.add(this.group);
    this.setImages(images);
    this.loadTextureArrayIndex();

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
      const record: TextureArrayRecord = { image, pointIndex: index, position, lastDistance: Infinity, baseHeight };
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
    if (this.textureArrayIndex) this.applyTextureArrayIndex(this.textureArrayIndex);
  }

  update(camera: PerspectiveCamera, deltaSeconds: number): void {
    this.frame += 1;
    this.updateAccumulator += deltaSeconds;
    this.updateBillboards(camera);
    this.applyViewportHeightCap(camera);
    this.pumpPageQueue();

    if (this.updateAccumulator >= 0.12) {
      this.updateAccumulator = 0;
      this.updateCards(camera.position, camera.quaternion);
      this.updateHover(camera);
    }
  }

  setSelected(id: string | null): void {
    this.selectedId = id;
    if (id) {
      const record = this.records.get(id);
      if (record) {
        this.desiredRecords = [record, ...this.desiredRecords.filter((candidate) => candidate.image.id !== id)];
        if (record.textureArray) this.requestPage(record.textureArray.page);
      }
    }
  }

  pick(): PositionedImage | null {
    const cardIntersections = this.raycaster.intersectObjects([...this.pageViews.values()].map((view) => view.mesh), false);
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
        textureArrayReady: this.textureArrayIndex !== null,
        textureArrayPagesLoaded: this.pageViews.size,
        textureQueue: this.textureQueueStats(),
      };
    }
    const startedAt = performance.now();
    for (const record of this.records.values()) record.lastDistance = record.position.distanceTo(camera.position);
    return this.debugSnapshot(performance.now() - startedAt);
  }

  dispose(): void {
    this.domElement.removeEventListener('pointermove', this.onPointerMove);
    this.domElement.removeEventListener('click', this.onClick);
    this.clear();
    this.scene.remove(this.group);
    for (const view of this.pageViews.values()) view.texture.dispose();
    this.pageViews.clear();
  }

  private async loadTextureArrayIndex(): Promise<void> {
    try {
      const response = await fetch(this.options.textureArrayIndexUrl);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const loaded = await response.json() as TextureArrayIndex;
      this.textureArrayIndex = loaded;
      this.applyTextureArrayIndex(loaded);
    } catch (error) {
      console.warn('textureArray index unavailable; falling back to points only', error);
    }
  }

  private applyTextureArrayIndex(index: TextureArrayIndex): void {
    this.pageByIndex.clear();
    for (const page of index.pages) this.pageByIndex.set(page.index, page);
    const entryById = new Map(index.entries.map((entry) => [entry.id, entry]));
    for (const record of this.records.values()) record.textureArray = entryById.get(record.image.id);
    const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
    if (camera) this.updateCards(camera.position, camera.quaternion);
  }

  private updateCards(cameraPosition: Vector3, cameraQuaternion: Quaternion): void {
    const startedAt = performance.now();
    const records = [...this.records.values()];
    for (const record of records) record.lastDistance = record.position.distanceTo(cameraPosition);

    this.desiredRecords = records
      .filter((record) => record.textureArray && record.lastDistance <= this.options.lazyLoadDistance)
      .sort((a, b) => a.lastDistance - b.lastDistance)
      .slice(0, this.options.maxTexturedCards);
    if (this.selectedId) {
      const selected = this.records.get(this.selectedId);
      if (selected?.textureArray && !this.desiredRecords.some((record) => record.image.id === selected.image.id)) {
        this.desiredRecords.unshift(selected);
      }
    }

    for (const record of this.desiredRecords) {
      if (record.textureArray) this.requestPage(record.textureArray.page);
    }
    this.evictUnusedPages();
    this.rebuildPageInstances(cameraQuaternion);
    this.debugStats = this.debugSnapshot(performance.now() - startedAt);
  }

  private requestPage(index: number): void {
    if (this.pageViews.has(index) || this.pageQueued.has(index) || this.pageLoading.has(index)) return;
    if (!this.pageByIndex.has(index)) return;
    this.pageQueued.add(index);
    this.pageQueue.push(index);
    this.totalPageRequests += 1;
    this.pumpPageQueue();
  }

  private pumpPageQueue(): void {
    while (this.activePageLoads < this.options.textureArrayPageConcurrency && this.pageQueue.length > 0) {
      const pageIndex = this.pageQueue.shift();
      if (pageIndex === undefined) continue;
      const page = this.pageByIndex.get(pageIndex);
      this.pageQueued.delete(pageIndex);
      if (!page || this.pageViews.has(pageIndex)) continue;
      this.activePageLoads += 1;
      this.pageLoading.add(pageIndex);
      void loadTextureArrayPage(page, this.textureArrayIndex!)
        .then((texture) => {
          this.activePageLoads -= 1;
          this.pageLoading.delete(pageIndex);
          this.totalPageLoads += 1;
          this.addPageView(page, texture);
          const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
          if (camera) this.rebuildPageInstances(camera.quaternion);
          this.pumpPageQueue();
        })
        .catch(() => {
          this.activePageLoads -= 1;
          this.pageLoading.delete(pageIndex);
          this.totalPageErrors += 1;
          this.pumpPageQueue();
        });
    }
  }

  private addPageView(page: TextureArrayIndexPage, texture: DataArrayTexture): void {
    if (this.pageViews.has(page.index)) {
      texture.dispose();
      return;
    }
    const geometry = new PlaneGeometry(1, 1);
    const capacity = Math.max(1, this.textureArrayIndex?.layersPerPage ?? 256);
    const layer = new InstancedBufferAttribute(new Float32Array(capacity), 1);
    layer.setUsage(DynamicDrawUsage);
    geometry.setAttribute('instanceLayer', layer);
    const material = createTextureArrayMaterial(texture);
    const mesh = new InstancedMesh(geometry, material, capacity);
    mesh.count = 0;
    mesh.frustumCulled = false;
    this.cardGroup.add(mesh);
    this.pageViews.set(page.index, { page, texture, mesh, layer, visibleIds: new Set(), lastUsedFrame: this.frame });
  }

  private rebuildPageInstances(cameraQuaternion: Quaternion): void {
    const recordsByPage = new Map<number, TextureArrayRecord[]>();
    for (const record of this.desiredRecords) {
      if (!record.textureArray || !this.pageViews.has(record.textureArray.page)) continue;
      const pageRecords = recordsByPage.get(record.textureArray.page) ?? [];
      pageRecords.push(record);
      recordsByPage.set(record.textureArray.page, pageRecords);
    }

    const scratch = new Object3D();
    for (const [pageIndex, view] of this.pageViews) {
      const records = recordsByPage.get(pageIndex) ?? [];
      view.visibleIds = new Set(records.map((record) => record.image.id));
      view.lastUsedFrame = records.length > 0 ? this.frame : view.lastUsedFrame;
      view.mesh.count = Math.min(records.length, view.mesh.instanceMatrix.count);
      for (let index = 0; index < view.mesh.count; index += 1) {
        const record = records[index]!;
        const textureArray = record.textureArray!;
        const [width, height] = this.getCardDimensions(record.image);
        scratch.position.copy(record.position);
        scratch.quaternion.copy(cameraQuaternion);
        scratch.scale.set(width, height, 1);
        scratch.updateMatrix();
        view.mesh.setMatrixAt(index, scratch.matrix);
        view.layer.setX(index, textureArray.layer);
      }
      view.mesh.instanceMatrix.needsUpdate = true;
      view.layer.needsUpdate = true;
    }
  }

  private evictUnusedPages(): void {
    if (this.pageViews.size <= this.options.textureArrayMaxPages) return;
    const desiredPages = new Set(this.desiredRecords.map((record) => record.textureArray?.page).filter((page): page is number => page !== undefined));
    const evictionCandidates = [...this.pageViews.values()]
      .filter((view) => !desiredPages.has(view.page.index))
      .sort((a, b) => a.lastUsedFrame - b.lastUsedFrame);
    for (const view of evictionCandidates) {
      if (this.pageViews.size <= this.options.textureArrayMaxPages) return;
      this.cardGroup.remove(view.mesh);
      view.mesh.geometry.dispose();
      view.mesh.material.dispose();
      view.texture.dispose();
      this.pageViews.delete(view.page.index);
    }
  }

  private debugSnapshot(lastUpdateMs: number): NonNullable<ViewerDebugStats['lod']> {
    const records = [...this.records.values()];
    const activeCards = this.desiredRecords.length;
    const loadedCards = [...this.pageViews.values()].reduce((total, view) => total + view.visibleIds.size, 0);
    const unloaded = records.filter((record) => !this.desiredRecords.some((desired) => desired.image.id === record.image.id));
    const nearestUnloadedDistance = unloaded.reduce<number | null>(
      (nearest, record) => (nearest === null ? record.lastDistance : Math.min(nearest, record.lastDistance)),
      null,
    );
    const candidateCount = records.filter((record) => record.lastDistance <= this.options.lazyLoadDistance).length;
    return {
      activeCards,
      loadedCards,
      capacity: Math.max(0, this.options.maxTexturedCards - activeCards),
      candidateCount,
      nearestUnloadedDistance,
      lazyLoadDistance: this.options.lazyLoadDistance,
      textureUnloadDistance: this.options.textureUnloadDistance,
      maxTexturedCards: this.options.maxTexturedCards,
      maxLoadedTextures: this.options.maxLoadedTextures,
      lastUpdateMs,
      textureArrayReady: this.textureArrayIndex !== null,
      textureArrayPagesLoaded: this.pageViews.size,
      textureQueue: this.textureQueueStats(),
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
      textureArrayReady: false,
      textureArrayPagesLoaded: 0,
      textureQueue: this.textureQueueStats(),
    };
  }

  private textureQueueStats(): TextureQueueDebugStats {
    return {
      activeLoads: this.activePageLoads,
      queued: this.pageQueued.size,
      loading: this.pageLoading.size,
      loaded: this.pageViews.size,
      totalRequests: this.totalPageRequests,
      totalLoads: this.totalPageLoads,
      totalErrors: this.totalPageErrors,
    };
  }

  private updateBillboards(camera: PerspectiveCamera): void {
    if (!this.options.billboard) return;
    for (const view of this.pageViews.values()) {
      if (view.mesh.count === 0) continue;
      // Instance matrices are rebuilt on the next LOD tick; this keeps work bounded.
      view.mesh.quaternion.identity();
    }
    camera.updateMatrixWorld(true);
  }

  private applyViewportHeightCap(camera: PerspectiveCamera): void {
    if (!Number.isFinite(this.options.maxViewportHeight)) return;
    camera.updateMatrixWorld(true);
    camera.getWorldDirection(this.cameraForward).normalize();
    for (const record of this.desiredRecords) {
      this.spriteOffset.subVectors(record.position, camera.position);
      const depth = this.spriteOffset.dot(this.cameraForward);
      const scale = viewportHeightScaleForCap({
        spriteWorldHeight: record.baseHeight,
        depth,
        cameraFovDegrees: camera.fov,
        maxViewportHeight: this.options.maxViewportHeight,
      });
      if (scale < 1) record.baseHeight *= scale;
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
    for (const view of this.pageViews.values()) {
      view.mesh.count = 0;
      view.visibleIds.clear();
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
    this.desiredRecords = [];
  }

  private getCardDimensions(image: PositionedImage): [number, number] {
    const height = Math.max(this.options.minSize, this.options.size);
    const rawAspect = image.width && image.height ? image.width / image.height : 1;
    const aspect = Math.min(this.options.maxAspectRatio, Math.max(1 / this.options.maxAspectRatio, rawAspect));
    return [height * aspect, height];
  }
}

function createTextureArrayMaterial(texture: DataArrayTexture): ShaderMaterial {
  return new ShaderMaterial({
    glslVersion: GLSL3,
    uniforms: { mapArray: { value: texture } },
    vertexShader: `
      in float instanceLayer;
      out vec2 vUv;
      flat out int vLayer;
      void main() {
        vUv = uv;
        vLayer = int(instanceLayer + 0.5);
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      precision highp sampler2DArray;
      uniform sampler2DArray mapArray;
      in vec2 vUv;
      flat in int vLayer;
      out vec4 outColor;
      void main() {
        vec4 color = texture(mapArray, vec3(vec2(vUv.x, 1.0 - vUv.y), float(vLayer)));
        outColor = vec4(color.rgb, 1.0);
      }
    `,
    transparent: false,
    side: DoubleSide,
    depthWrite: true,
  });
}

async function loadTextureArrayPage(
  page: TextureArrayIndexPage,
  index: TextureArrayIndex,
): Promise<DataArrayTexture> {
  const response = await fetch(page.url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const sourceCanvas = document.createElement('canvas');
    sourceCanvas.width = bitmap.width;
    sourceCanvas.height = bitmap.height;
    const sourceContext = sourceCanvas.getContext('2d', { willReadFrequently: true });
    if (!sourceContext) throw new Error('2D canvas unavailable');
    sourceContext.drawImage(bitmap, 0, 0);
    const source = sourceContext.getImageData(0, 0, bitmap.width, bitmap.height).data;
    const layerCount = Math.max(1, page.layers);
    const thumbSize = index.thumbSize;
    const data = new Uint8Array(thumbSize * thumbSize * layerCount * 4);

    for (let layer = 0; layer < layerCount; layer += 1) {
      const col = layer % index.cols;
      const row = Math.floor(layer / index.cols);
      const sourceX = col * thumbSize;
      const sourceY = row * thumbSize;
      const layerOffset = layer * thumbSize * thumbSize * 4;
      for (let y = 0; y < thumbSize; y += 1) {
        const sourceOffset = ((sourceY + y) * bitmap.width + sourceX) * 4;
        const targetOffset = layerOffset + y * thumbSize * 4;
        data.set(source.subarray(sourceOffset, sourceOffset + thumbSize * 4), targetOffset);
      }
    }

    const texture = new DataArrayTexture(data, thumbSize, thumbSize, layerCount);
    texture.format = RGBAFormat;
    texture.type = UnsignedByteType;
    texture.colorSpace = SRGBColorSpace;
    texture.minFilter = LinearFilter;
    texture.magFilter = LinearFilter;
    texture.generateMipmaps = false;
    texture.unpackAlignment = 1;
    texture.needsUpdate = true;
    return texture;
  } finally {
    bitmap.close();
  }
}
