import { SRGBColorSpace, Texture, TextureLoader } from 'three';

import type { TextureQueueDebugStats } from './types';

export interface TextureRequest {
  id: string;
  url: string;
  onLoad: (texture: Texture) => void;
  onError?: (error: unknown) => void;
  priority?: boolean;
}

interface QueuedRequest extends TextureRequest {
  cancelled: boolean;
}

export class TextureLoadQueue {
  private readonly loader = new TextureLoader();
  private readonly loaded = new Map<string, Texture>();
  private readonly loading = new Set<string>();
  private readonly queued = new Set<string>();
  private readonly queue: QueuedRequest[] = [];
  private activeLoads = 0;
  private destroyed = false;
  private totalRequests = 0;
  private totalLoads = 0;
  private totalErrors = 0;

  constructor(private readonly maxConcurrentLoads = 8) {
    this.loader.setCrossOrigin('anonymous');
  }

  request(request: TextureRequest): () => void {
    if (this.destroyed) return () => undefined;
    this.totalRequests += 1;

    const loaded = this.loaded.get(request.id);
    if (loaded) {
      request.onLoad(loaded);
      return () => undefined;
    }

    const queued: QueuedRequest = { ...request, cancelled: false };
    this.queued.add(request.id);
    if (request.priority === true) {
      this.queue.unshift(queued);
    } else {
      this.queue.push(queued);
    }
    this.pump();

    return () => {
      queued.cancelled = true;
      this.queued.delete(request.id);
    };
  }

  has(id: string): boolean {
    return this.loaded.has(id) || this.loading.has(id) || this.queued.has(id);
  }

  get(id: string): Texture | undefined {
    return this.loaded.get(id);
  }

  disposeTexture(id: string): void {
    const texture = this.loaded.get(id);
    if (!texture) return;
    texture.dispose();
    this.loaded.delete(id);
  }

  getDebugStats(): TextureQueueDebugStats {
    return {
      activeLoads: this.activeLoads,
      queued: this.queued.size,
      loading: this.loading.size,
      loaded: this.loaded.size,
      totalRequests: this.totalRequests,
      totalLoads: this.totalLoads,
      totalErrors: this.totalErrors,
    };
  }

  dispose(): void {
    this.destroyed = true;
    this.queue.length = 0;
    this.queued.clear();
    for (const texture of this.loaded.values()) {
      texture.dispose();
    }
    this.loaded.clear();
    this.loading.clear();
  }

  private pump(): void {
    if (this.destroyed) return;
    while (this.activeLoads < this.maxConcurrentLoads && this.queue.length > 0) {
      const request = this.queue.shift();
      if (!request) continue;
      this.queued.delete(request.id);
      if (request.cancelled) continue;
      if (this.loaded.has(request.id)) {
        request.onLoad(this.loaded.get(request.id)!);
        continue;
      }
      if (this.loading.has(request.id)) continue;

      this.activeLoads += 1;
      this.loading.add(request.id);
      this.loader.load(
        request.url,
        (texture) => {
          this.activeLoads -= 1;
          this.loading.delete(request.id);
          texture.colorSpace = SRGBColorSpace;
          texture.needsUpdate = true;
          if (this.destroyed || request.cancelled) {
            texture.dispose();
          } else {
            this.totalLoads += 1;
            this.loaded.set(request.id, texture);
            request.onLoad(texture);
          }
          this.pump();
        },
        undefined,
        (error) => {
          this.activeLoads -= 1;
          this.loading.delete(request.id);
          if (!request.cancelled) {
            this.totalErrors += 1;
            request.onError?.(error);
          }
          this.pump();
        },
      );
    }
  }
}
