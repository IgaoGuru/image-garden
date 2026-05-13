import type {
  ConstellationData,
  ConstellationDataSource,
  ConstellationImage,
  IndexStatus,
  NearbyQuery,
  RuntimeAsset,
  RuntimeAssetMetadata,
  Vec3,
} from './types';

export interface StaticDataSourceOptions {
  status?: Partial<IndexStatus>;
}

export interface FetchDataSourceEndpoints {
  status: string;
  assets: string;
  nearAssets: string;
  asset: (id: string) => string;
}

export interface FetchDataSourceOptions {
  /** Base URL for an HTTP asset API, e.g. `http://127.0.0.1:8000`. Defaults to same-origin. */
  baseUrl?: string;
  /** Explicit REST endpoint paths. Defaults match the Image Garden/Studio local API adapter. */
  endpoints?: Partial<FetchDataSourceEndpoints>;
  /** Number of assets requested from the initial asset endpoint. */
  initialLimit?: number;
  initialOffset?: number;
  fetch?: typeof fetch;
}

export const STUDIO_API_ENDPOINTS: FetchDataSourceEndpoints = {
  status: '/api/status',
  assets: '/api/assets',
  nearAssets: '/api/assets/near',
  asset: (id: string) => `/api/assets/${encodeURIComponent(id)}`,
};

/** Convert runtime assets into the legacy `ConstellationData` shape consumed by `mount`. */
export function runtimeAssetsToData(assets: readonly RuntimeAsset[]): ConstellationData {
  return {
    images: assets.map((asset) => ({
      id: asset.id,
      url: asset.fullUrl ?? asset.thumbnailUrl,
      thumbnailUrl: asset.thumbnailUrl,
      fullUrl: asset.fullUrl,
      width: asset.width,
      height: asset.height,
      position: asset.position,
      metadata: asset.metadata,
    })),
  };
}

/** Convert a positioned image into the runtime contract, rejecting embedding-only inputs. */
export function imageToRuntimeAsset(image: ConstellationImage): RuntimeAsset {
  if (!image.position) {
    throw new Error(`Image ${image.id} must include a precomputed position for runtime data-source use.`);
  }
  const thumbnailUrl = image.thumbnailUrl ?? image.url ?? image.fullUrl;
  if (!thumbnailUrl) {
    throw new Error(`Image ${image.id} must include thumbnailUrl or url for runtime data-source use.`);
  }
  return {
    id: image.id,
    thumbnailUrl,
    fullUrl: image.fullUrl ?? image.url,
    width: image.width,
    height: image.height,
    position: image.position,
    metadata: image.metadata,
  };
}

export function createStaticDataSource(
  input: readonly RuntimeAsset[] | ConstellationData,
  options: StaticDataSourceOptions = {},
): ConstellationDataSource {
  const assets: RuntimeAsset[] = isConstellationData(input)
    ? input.images.map(imageToRuntimeAsset)
    : input.map((asset) => ({ ...asset }));
  validateRuntimeAssets(assets);
  const byId = new Map<string, RuntimeAsset>(assets.map((asset) => [asset.id, asset]));
  const status: IndexStatus = {
    state: 'ready',
    totalAssets: assets.length,
    indexedAssets: assets.length,
    ...options.status,
  };

  return {
    async getStatus() {
      return status;
    },
    async getInitialAssets() {
      return [...assets];
    },
    async getNearbyAssets(query: NearbyQuery) {
      const { x, y, z, radius, limit } = query;
      const radiusSq = radius * radius;
      return assets
        .map((asset) => {
          const dx = asset.position[0] - x;
          const dy = asset.position[1] - y;
          const dz = asset.position[2] - z;
          return { asset, distanceSq: dx * dx + dy * dy + dz * dz };
        })
        .filter(({ distanceSq }) => distanceSq <= radiusSq)
        .sort((a, b) => a.distanceSq - b.distanceSq)
        .slice(0, limit ?? assets.length)
        .map(({ asset }) => asset);
    },
    async getAsset(id: string) {
      return byId.get(id) ?? null;
    },
  };
}

export function createStudioDataSource(
  options: Omit<FetchDataSourceOptions, 'endpoints'> = {},
): ConstellationDataSource {
  return createFetchDataSource({ ...options, endpoints: STUDIO_API_ENDPOINTS });
}

export function createFetchDataSource(options: FetchDataSourceOptions = {}): ConstellationDataSource {
  const baseUrl = normalizeBaseUrl(options.baseUrl ?? '');
  const fetchImpl = options.fetch ?? fetch;
  const endpoints: FetchDataSourceEndpoints = { ...STUDIO_API_ENDPOINTS, ...options.endpoints };

  return {
    async getStatus() {
      return parseStatus(await fetchJson(fetchImpl, endpointUrl(baseUrl, endpoints.status)));
    },
    async getInitialAssets() {
      const params = new URLSearchParams();
      if (options.initialLimit !== undefined) params.set('limit', String(options.initialLimit));
      if (options.initialOffset !== undefined) params.set('offset', String(options.initialOffset));
      return parseAssets(await fetchJson(fetchImpl, withQuery(endpointUrl(baseUrl, endpoints.assets), params)));
    },
    async getNearbyAssets(query: NearbyQuery) {
      const params = new URLSearchParams({
        x: String(query.x),
        y: String(query.y),
        z: String(query.z),
        radius: String(query.radius),
      });
      if (query.limit !== undefined) params.set('limit', String(query.limit));
      return parseAssets(await fetchJson(fetchImpl, withQuery(endpointUrl(baseUrl, endpoints.nearAssets), params)));
    },
    async getAsset(id: string) {
      const value = await fetchJson(fetchImpl, endpointUrl(baseUrl, endpoints.asset(id)), { allowNotFound: true });
      return value === null ? null : parseAsset(value);
    },
  };
}

export interface SyntheticAssetOptions {
  count?: number;
  radius?: number;
  thumbnailUrl?: (index: number) => string;
}

export function createSyntheticRuntimeAssets(options: SyntheticAssetOptions = {}): RuntimeAsset[] {
  const count = options.count ?? 10_000;
  const radius = options.radius ?? 900;
  return Array.from({ length: count }, (_, index) => {
    const position = fibonacciSphere(index, count, radius);
    const hue = Math.round((index * 137.508) % 360);
    return {
      id: `synthetic-${index}`,
      thumbnailUrl: options.thumbnailUrl?.(index) ?? makeSvgDataUrl(`asset ${index}`, `hsl(${hue} 75% 58%)`),
      width: 256,
      height: 256,
      position,
      metadata: { synthetic: true, ordinal: index },
    };
  });
}

function isConstellationData(input: readonly RuntimeAsset[] | ConstellationData): input is ConstellationData {
  return !Array.isArray(input) && Array.isArray((input as Partial<ConstellationData>).images);
}

function validateRuntimeAssets(assets: readonly RuntimeAsset[]): void {
  const ids = new Set<string>();
  for (const [index, asset] of assets.entries()) {
    if (typeof asset.id !== 'string' || asset.id.length === 0) {
      throw new Error(`Asset at index ${index} must have a non-empty string id.`);
    }
    if (ids.has(asset.id)) {
      throw new Error(`Duplicate asset id: ${asset.id}`);
    }
    ids.add(asset.id);
    if (typeof asset.thumbnailUrl !== 'string' || asset.thumbnailUrl.length === 0) {
      throw new Error(`Asset ${asset.id} must have a non-empty thumbnailUrl.`);
    }
    parseVec3(asset.position, `asset ${asset.id} position`);
  }
}

async function fetchJson(
  fetchImpl: typeof fetch,
  url: string,
  options: { allowNotFound?: boolean } = {},
): Promise<unknown> {
  const response = await fetchImpl(url);
  if (response.status === 404 && options.allowNotFound) return null;
  if (!response.ok) {
    throw new Error(`Request failed ${response.status} ${response.statusText}: ${url}`);
  }
  return response.json() as Promise<unknown>;
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
}

function endpointUrl(baseUrl: string, endpoint: string): string {
  if (/^https?:\/\//.test(endpoint)) return endpoint;
  const path = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  return `${baseUrl}${path}`;
}

function withQuery(url: string, params: URLSearchParams): string {
  const query = params.toString();
  if (!query) return url;
  return `${url}${url.includes('?') ? '&' : '?'}${query}`;
}

function parseStatus(value: unknown): IndexStatus {
  const object = asRecord(value, 'status response');
  const state = typeof object.state === 'string' ? object.state : 'unknown';
  return {
    state,
    totalAssets: optionalNumber(object.totalAssets),
    indexedAssets: optionalNumber(object.indexedAssets),
    message: typeof object.message === 'string' ? object.message : undefined,
    updatedAt: typeof object.updatedAt === 'string' ? object.updatedAt : undefined,
  };
}

function parseAssets(value: unknown): RuntimeAsset[] {
  const arrayValue = Array.isArray(value) ? value : asArrayProperty(value, 'assets') ?? asArrayProperty(value, 'images');
  if (!arrayValue) throw new Error('Asset response must be an array or contain an assets/images array.');
  return arrayValue.map(parseAsset);
}

function parseAsset(value: unknown): RuntimeAsset {
  const object = asRecord(value, 'asset');
  const id = requiredString(object.id, 'asset.id');
  const thumbnailUrl = requiredString(object.thumbnailUrl ?? object.url, `asset ${id} thumbnailUrl`);
  return {
    id,
    thumbnailUrl,
    fullUrl: typeof object.fullUrl === 'string' ? object.fullUrl : typeof object.url === 'string' ? object.url : undefined,
    width: optionalNumber(object.width),
    height: optionalNumber(object.height),
    position: parseVec3(object.position, `asset ${id} position`),
    metadata: parseMetadata(object.metadata),
  };
}

function parseMetadata(value: unknown): RuntimeAssetMetadata | undefined {
  if (value === undefined) return undefined;
  return { ...asRecord(value, 'asset.metadata') };
}

function parseVec3(value: unknown, label: string): Vec3 {
  if (!Array.isArray(value) || value.length !== 3) throw new Error(`${label} must be a [x, y, z] array.`);
  const vector = value.map((entry, index) => {
    if (typeof entry !== 'number' || !Number.isFinite(entry)) {
      throw new Error(`${label}[${index}] must be a finite number.`);
    }
    return entry;
  });
  return [vector[0] ?? 0, vector[1] ?? 0, vector[2] ?? 0];
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function asArrayProperty(value: unknown, property: string): unknown[] | undefined {
  const object = asRecord(value, 'asset response');
  const arrayValue = object[property];
  return Array.isArray(arrayValue) ? arrayValue : undefined;
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) throw new Error(`${label} must be a non-empty string.`);
  return value;
}

function optionalNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function fibonacciSphere(index: number, count: number, radius: number): Vec3 {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (index / Math.max(1, count - 1)) * 2;
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = goldenAngle * index;
  return [Math.cos(theta) * r * radius, y * radius, Math.sin(theta) * r * radius];
}

function makeSvgDataUrl(label: string, color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256" rx="24" fill="${color}"/><circle cx="72" cy="64" r="34" fill="white" fill-opacity="0.22"/><text x="128" y="136" text-anchor="middle" font-family="system-ui,sans-serif" font-size="26" font-weight="700" fill="white">${label}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
