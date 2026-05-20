import {
  BufferGeometry,
  DoubleSide,
  DynamicDrawUsage,
  Float32BufferAttribute,
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
  SRGBColorSpace,
  Texture,
  TextureLoader,
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

interface AtlasIndexEntry {
  id: string;
  page: number;
  u0: number;
  v0: number;
  u1: number;
  v1: number;
}

interface AtlasIndexPage {
  index: number;
  url: string;
}

interface AtlasIndex {
  entries: AtlasIndexEntry[];
  pages: AtlasIndexPage[];
  pageCapacity: number;
}

interface AtlasRecord {
  image: PositionedImage;
  pointIndex: number;
  position: Vector3;
  atlas?: AtlasIndexEntry;
  lastDistance: number;
  baseHeight: number;
}

interface AtlasPageView {
  page: AtlasIndexPage;
  texture: Texture;
  mesh: InstancedMesh<PlaneGeometry, ShaderMaterial>;
  uvOffset: InstancedBufferAttribute;
  uvScale: InstancedBufferAttribute;
  visibleIds: Set<string>;
  lastUsedFrame: number;
}

export interface AtlasLodManagerOptions extends SpriteOptions {
  onSelect?: (image: ConstellationImage) => void;
  onHover?: (image: ConstellationImage | null) => void;
}

export class AtlasLodManager {
  private readonly group = new Group();
  private readonly cardGroup = new Group();
  private readonly raycaster = new Raycaster();
  private readonly pointer = new Vector2(0, 0);
  private readonly cameraForward = new Vector3();
  private readonly spriteOffset = new Vector3();
  private readonly records = new Map<string, AtlasRecord>();
  private readonly recordsByPointIndex: AtlasRecord[] = [];
  private readonly loader = new TextureLoader();
  private readonly pageQueue: number[] = [];
  private readonly pageQueued = new Set<number>();
  private readonly pageLoading = new Set<number>();
  private readonly pageViews = new Map<number, AtlasPageView>();
  private readonly pageByIndex = new Map<number, AtlasIndexPage>();
  private readonly domElement: HTMLElement;
  private readonly options: Required<
    Omit<
      SpriteOptions,
      | 'renderMode'
      | 'lodThreshold'
      | 'selectedColor'
      | 'textureUnloadDistance'
      | 'pointColor'
      | 'minCardScreenHeightPx'
      | 'frustumCullCards'
      | 'frustumCullMargin'
      | 'textureArray'
      | 'textureArrayIndexUrl'
      | 'textureArrayPageConcurrency'
      | 'textureArrayMaxPages'
      | 'highRes'
      | 'highResDistance'
      | 'highResScreenHeightPx'
      | 'highResUnloadDistance'
      | 'highResMaxTextures'
      | 'highResMaxConcurrentLoads'
      | 'atlasIndexUrl'
    >
  > & {
    selectedColor: number;
    textureUnloadDistance: number;
    pointColor: number;
    atlasIndexUrl: string | null;
    atlasPageConcurrency: number;
    atlasMaxPages: number;
  };
  private readonly onSelect?: (image: ConstellationImage) => void;
  private readonly onHover?: (image: ConstellationImage | null) => void;
  private points: Points<BufferGeometry, PointsMaterial> | null = null;
  private atlasIndex: AtlasIndex | null = null;
  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private updateAccumulator = 0.25;
  private frame = 0;
  private activePageLoads = 0;
  private totalPageRequests = 0;
  private totalPageLoads = 0;
  private totalPageErrors = 0;
  private desiredRecords: AtlasRecord[] = [];
  private debugStats: NonNullable<ViewerDebugStats['lod']> | null = null;

  constructor(
    private readonly scene: Scene,
    domElement: HTMLElement,
    images: PositionedImage[],
    options: AtlasLodManagerOptions = {},
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
      atlas: options.atlas ?? false,
      atlasIndexUrl: options.atlasIndexUrl ?? null,
      atlasPageConcurrency: options.atlasPageConcurrency ?? 4,
      atlasMaxPages: options.atlasMaxPages ?? 16,
    };
    this.onSelect = options.onSelect;
    this.onHover = options.onHover;
    this.debugStats = this.emptyDebugStats();
    this.raycaster.params.Points = { threshold: this.options.pointPickRadius };
    this.group.add(this.cardGroup);
    this.scene.add(this.group);
    this.setImages(images);
    if (this.options.atlasIndexUrl) void this.loadAtlasIndex();

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
      const record: AtlasRecord = { image, pointIndex: index, position, lastDistance: Infinity, baseHeight };
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
    if (this.atlasIndex) this.applyAtlasIndex(this.atlasIndex);
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
        if (record.atlas) this.requestPage(record.atlas.page);
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
        atlasReady: this.atlasIndex !== null,
        atlasPagesLoaded: this.pageViews.size,
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

  private async loadAtlasIndex(): Promise<void> {
    try {
      if (!this.options.atlasIndexUrl) return;
      const response = await fetch(this.options.atlasIndexUrl);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const loaded = await response.json() as AtlasIndex;
      this.atlasIndex = loaded;
      this.applyAtlasIndex(loaded);
    } catch (error) {
      console.warn('atlas index unavailable; falling back to points only', error);
    }
  }

  private applyAtlasIndex(index: AtlasIndex): void {
    this.pageByIndex.clear();
    for (const page of index.pages) this.pageByIndex.set(page.index, page);
    const entryById = new Map(index.entries.map((entry) => [entry.id, entry]));
    for (const record of this.records.values()) record.atlas = entryById.get(record.image.id);
    const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
    if (camera) this.updateCards(camera.position, camera.quaternion);
  }

  private updateCards(cameraPosition: Vector3, cameraQuaternion: Quaternion): void {
    const startedAt = performance.now();
    const records = [...this.records.values()];
    for (const record of records) record.lastDistance = record.position.distanceTo(cameraPosition);

    this.desiredRecords = records
      .filter((record) => record.atlas && record.lastDistance <= this.options.lazyLoadDistance)
      .sort((a, b) => a.lastDistance - b.lastDistance)
      .slice(0, this.options.maxTexturedCards);
    if (this.selectedId) {
      const selected = this.records.get(this.selectedId);
      if (selected?.atlas && !this.desiredRecords.some((record) => record.image.id === selected.image.id)) {
        this.desiredRecords.unshift(selected);
      }
    }

    for (const record of this.desiredRecords) {
      if (record.atlas) this.requestPage(record.atlas.page);
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
    while (this.activePageLoads < this.options.atlasPageConcurrency && this.pageQueue.length > 0) {
      const pageIndex = this.pageQueue.shift();
      if (pageIndex === undefined) continue;
      const page = this.pageByIndex.get(pageIndex);
      this.pageQueued.delete(pageIndex);
      if (!page || this.pageViews.has(pageIndex)) continue;
      this.activePageLoads += 1;
      this.pageLoading.add(pageIndex);
      this.loader.load(
        page.url,
        (texture) => {
          this.activePageLoads -= 1;
          this.pageLoading.delete(pageIndex);
          texture.colorSpace = SRGBColorSpace;
          texture.minFilter = LinearFilter;
          texture.magFilter = LinearFilter;
          texture.needsUpdate = true;
          this.totalPageLoads += 1;
          this.addPageView(page, texture);
          const camera = this.scene.userData.camera as PerspectiveCamera | undefined;
          if (camera) this.rebuildPageInstances(camera.quaternion);
          this.pumpPageQueue();
        },
        undefined,
        () => {
          this.activePageLoads -= 1;
          this.pageLoading.delete(pageIndex);
          this.totalPageErrors += 1;
          this.pumpPageQueue();
        },
      );
    }
  }

  private addPageView(page: AtlasIndexPage, texture: Texture): void {
    if (this.pageViews.has(page.index)) {
      texture.dispose();
      return;
    }
    const geometry = new PlaneGeometry(1, 1);
    const capacity = Math.max(1, this.atlasIndex?.pageCapacity ?? 1024);
    const uvOffset = new InstancedBufferAttribute(new Float32Array(capacity * 2), 2);
    const uvScale = new InstancedBufferAttribute(new Float32Array(capacity * 2), 2);
    uvOffset.setUsage(DynamicDrawUsage);
    uvScale.setUsage(DynamicDrawUsage);
    geometry.setAttribute('instanceUvOffset', uvOffset);
    geometry.setAttribute('instanceUvScale', uvScale);
    const material = createAtlasMaterial(texture);
    const mesh = new InstancedMesh(geometry, material, capacity);
    mesh.count = 0;
    mesh.frustumCulled = false;
    this.cardGroup.add(mesh);
    this.pageViews.set(page.index, { page, texture, mesh, uvOffset, uvScale, visibleIds: new Set(), lastUsedFrame: this.frame });
  }

  private rebuildPageInstances(cameraQuaternion: Quaternion): void {
    const recordsByPage = new Map<number, AtlasRecord[]>();
    for (const record of this.desiredRecords) {
      if (!record.atlas || !this.pageViews.has(record.atlas.page)) continue;
      const pageRecords = recordsByPage.get(record.atlas.page) ?? [];
      pageRecords.push(record);
      recordsByPage.set(record.atlas.page, pageRecords);
    }

    const scratch = new Object3D();
    for (const [pageIndex, view] of this.pageViews) {
      const records = recordsByPage.get(pageIndex) ?? [];
      view.visibleIds = new Set(records.map((record) => record.image.id));
      view.lastUsedFrame = records.length > 0 ? this.frame : view.lastUsedFrame;
      view.mesh.count = Math.min(records.length, view.mesh.instanceMatrix.count);
      for (let index = 0; index < view.mesh.count; index += 1) {
        const record = records[index]!;
        const atlas = record.atlas!;
        const [width, height] = this.getCardDimensions(record.image);
        scratch.position.copy(record.position);
        scratch.quaternion.copy(cameraQuaternion);
        scratch.scale.set(width, height, 1);
        scratch.updateMatrix();
        view.mesh.setMatrixAt(index, scratch.matrix);
        view.uvOffset.setXY(index, atlas.u0, atlas.v0);
        view.uvScale.setXY(index, atlas.u1 - atlas.u0, atlas.v1 - atlas.v0);
      }
      view.mesh.instanceMatrix.needsUpdate = true;
      view.uvOffset.needsUpdate = true;
      view.uvScale.needsUpdate = true;
    }
  }

  private evictUnusedPages(): void {
    if (this.pageViews.size <= this.options.atlasMaxPages) return;
    const desiredPages = new Set(this.desiredRecords.map((record) => record.atlas?.page).filter((page): page is number => page !== undefined));
    const evictionCandidates = [...this.pageViews.values()]
      .filter((view) => !desiredPages.has(view.page.index))
      .sort((a, b) => a.lastUsedFrame - b.lastUsedFrame);
    for (const view of evictionCandidates) {
      if (this.pageViews.size <= this.options.atlasMaxPages) return;
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
      atlasReady: this.atlasIndex !== null,
      atlasPagesLoaded: this.pageViews.size,
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
      atlasReady: false,
      atlasPagesLoaded: 0,
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

function createAtlasMaterial(texture: Texture): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { map: { value: texture }, opacity: { value: 1 } },
    vertexShader: `
      attribute vec2 instanceUvOffset;
      attribute vec2 instanceUvScale;
      varying vec2 vAtlasUv;
      void main() {
        vAtlasUv = instanceUvOffset + uv * instanceUvScale;
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform sampler2D map;
      uniform float opacity;
      varying vec2 vAtlasUv;
      void main() {
        vec4 color = texture2D(map, vAtlasUv);
        gl_FragColor = vec4(color.rgb, color.a * opacity);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
    transparent: true,
    side: DoubleSide,
    depthWrite: false,
  });
}
