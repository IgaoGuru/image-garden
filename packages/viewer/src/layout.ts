import { UMAP } from 'umap-js';

import type { ConstellationData, LayoutOptions, PositionedImage, Vec3 } from './types';

const DEFAULT_SCALE = 120;

export function computeLayout(
  data: ConstellationData,
  options: LayoutOptions = {},
): PositionedImage[] {
  const images = data.images;
  if (images.length === 0) return [];

  const center = options.center ?? true;

  if (images.every((image) => image.position !== undefined)) {
    const scale = options.scale ?? 1;
    const points = images.map((image) => {
      const position = image.position as Vec3;
      assertFiniteVector(position, 3, `Image ${image.id} position`);
      return position;
    });
    const transformed = transformPoints(points, { center, scale, normalize: false });
    const jittered = options.duplicateJitter
      ? jitterDuplicatePositions(
          transformed,
          images.map((image) => image.id),
          options,
        )
      : transformed;
    const relaxed = options.collisionRelaxation ? relaxCollisions(jittered, options) : jittered;
    return images.map((image, index) => ({ ...image, position: relaxed[index] ?? [0, 0, 0] }));
  }

  if (!images.every((image) => image.embedding !== undefined)) {
    throw new Error(
      'Every image must include either a precomputed `position` or an `embedding` so the viewer can compute a layout.',
    );
  }

  const embeddings = images.map((image) => {
    const embedding = image.embedding;
    if (!embedding || embedding.length === 0) {
      throw new Error(`Image ${image.id} has an empty embedding.`);
    }
    assertFiniteVector(embedding, undefined, `Image ${image.id} embedding`);
    return Array.from(embedding);
  });

  const dimensions = embeddings[0]?.length ?? 0;
  for (const [index, embedding] of embeddings.entries()) {
    if (embedding.length !== dimensions) {
      throw new Error(
        `Embedding dimension mismatch at image ${images[index]?.id ?? index}: expected ${dimensions}, got ${embedding.length}.`,
      );
    }
  }

  const scale = options.scale ?? DEFAULT_SCALE;
  const points = reduceEmbeddingsTo3D(embeddings, options);
  const transformed = transformPoints(points, { center, scale, normalize: true });
  const relaxed = options.collisionRelaxation ?? true ? relaxCollisions(transformed, options) : transformed;
  return images.map((image, index) => ({ ...image, position: relaxed[index] ?? [0, 0, 0] }));
}

function reduceEmbeddingsTo3D(embeddings: number[][], options: LayoutOptions): Vec3[] {
  if (embeddings.length === 1) return [[0, 0, 0]];
  if (embeddings.length === 2) return [[-0.5, 0, 0], [0.5, 0, 0]];

  const nNeighbors = Math.max(
    2,
    Math.min(options.nNeighbors ?? 15, embeddings.length - 1),
  );

  const umap = new UMAP({
    nComponents: 3,
    nNeighbors,
    minDist: options.minDist ?? 0.08,
    spread: options.spread ?? 1,
    random: seededRandom(options.seed ?? 42),
  });

  const result = umap.fit(embeddings);
  return result.map((point) => [point[0] ?? 0, point[1] ?? 0, point[2] ?? 0]);
}

function transformPoints(
  points: readonly Vec3[],
  options: { center: boolean; scale: number; normalize: boolean },
): Vec3[] {
  const bounds = getBounds(points);
  const center: Vec3 = [
    (bounds.min[0] + bounds.max[0]) / 2,
    (bounds.min[1] + bounds.max[1]) / 2,
    (bounds.min[2] + bounds.max[2]) / 2,
  ];
  const extents: Vec3 = [
    Math.max(1e-9, bounds.max[0] - bounds.min[0]),
    Math.max(1e-9, bounds.max[1] - bounds.min[1]),
    Math.max(1e-9, bounds.max[2] - bounds.min[2]),
  ];
  const maxExtent = Math.max(extents[0], extents[1], extents[2], 1e-9);
  const factor = options.normalize ? options.scale / maxExtent : options.scale;

  return points.map((point) => {
    const x = (options.center ? point[0] - center[0] : point[0]) * factor;
    const y = (options.center ? point[1] - center[1] : point[1]) * factor;
    const z = (options.center ? point[2] - center[2] : point[2]) * factor;
    return [x, y, z];
  });
}

export function jitterDuplicatePositions(
  points: readonly Vec3[],
  ids: readonly string[],
  options: LayoutOptions = {},
): Vec3[] {
  const nearDistance = options.duplicateJitterDistance ?? 12;
  const minShift = options.duplicateJitterMin ?? 50;
  const halfLife = options.duplicateJitterHalfLife ?? 50;
  const maxShift = options.duplicateJitterMax ?? 250;
  if (points.length < 2 || nearDistance <= 0 || minShift < 0 || halfLife <= 0) {
    return points.map((point) => [point[0], point[1], point[2]]);
  }

  const gridPoints = points.map((point) => [point[0], point[1], point[2]] as [number, number, number]);
  const grid = buildSpatialGrid(gridPoints, nearDistance);
  const nearDistanceSq = nearDistance * nearDistance;
  return points.map((point, index) => {
    const source = gridPoints[index];
    if (!source) return [point[0], point[1], point[2]];
    let hasNearDuplicate = false;
    forEachNeighborIndex(grid, gridCell(source, nearDistance), (otherIndex) => {
      if (otherIndex === index || hasNearDuplicate) return;
      const other = gridPoints[otherIndex];
      if (!other) return;
      const dx = source[0] - other[0];
      const dy = source[1] - other[1];
      const dz = source[2] - other[2];
      hasNearDuplicate = dx * dx + dy * dy + dz * dz <= nearDistanceSq;
    });
    if (!hasNearDuplicate) return [point[0], point[1], point[2]];

    const random = seededRandom(hashString(ids[index] ?? String(index)));
    const direction = randomUnitVector(random);
    const u = Math.min(1 - Number.EPSILON, Math.max(Number.EPSILON, random()));
    const lambda = Math.LN2 / halfLife;
    const magnitude = Math.min(maxShift, minShift + (-Math.log(1 - u) / lambda));
    return [
      point[0] + direction[0] * magnitude,
      point[1] + direction[1] * magnitude,
      point[2] + direction[2] * magnitude,
    ];
  });
}

export function relaxCollisions(points: readonly Vec3[], options: LayoutOptions = {}): Vec3[] {
  const minDistance = options.collisionDistance ?? 10;
  const iterations = options.collisionIterations ?? 35;
  const anchorStrength = options.collisionAnchorStrength ?? 0.025;
  if (points.length < 2 || minDistance <= 0 || iterations <= 0) {
    return points.map((point) => [point[0], point[1], point[2]]);
  }

  const original = points.map((point) => [point[0], point[1], point[2]] as [number, number, number]);
  const relaxed = points.map((point) => [point[0], point[1], point[2]] as [number, number, number]);
  const minDistanceSq = minDistance * minDistance;

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const grid = buildSpatialGrid(relaxed, minDistance);
    for (let index = 0; index < relaxed.length; index += 1) {
      const point = relaxed[index];
      if (!point) continue;
      const cell = gridCell(point, minDistance);
      forEachNeighborIndex(grid, cell, (otherIndex) => {
        if (otherIndex <= index) return;
        const other = relaxed[otherIndex];
        if (!other) return;
        separatePair(point, other, index, otherIndex, minDistance, minDistanceSq);
      });
    }

    if (anchorStrength > 0) {
      for (let index = 0; index < relaxed.length; index += 1) {
        const point = relaxed[index];
        const anchor = original[index];
        if (!point || !anchor) continue;
        point[0] += (anchor[0] - point[0]) * anchorStrength;
        point[1] += (anchor[1] - point[1]) * anchorStrength;
        point[2] += (anchor[2] - point[2]) * anchorStrength;
      }
    }
  }

  return relaxed.map((point) => [point[0], point[1], point[2]]);
}

function buildSpatialGrid(points: readonly [number, number, number][], cellSize: number): Map<string, number[]> {
  const grid = new Map<string, number[]>();
  for (const [index, point] of points.entries()) {
    const key = gridKey(gridCell(point, cellSize));
    const bucket = grid.get(key);
    if (bucket) {
      bucket.push(index);
    } else {
      grid.set(key, [index]);
    }
  }
  return grid;
}

function gridCell(point: readonly [number, number, number], cellSize: number): [number, number, number] {
  return [
    Math.floor(point[0] / cellSize),
    Math.floor(point[1] / cellSize),
    Math.floor(point[2] / cellSize),
  ];
}

function gridKey(cell: readonly [number, number, number]): string {
  return `${cell[0]},${cell[1]},${cell[2]}`;
}

function forEachNeighborIndex(
  grid: Map<string, number[]>,
  cell: readonly [number, number, number],
  callback: (index: number) => void,
): void {
  for (let dx = -1; dx <= 1; dx += 1) {
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dz = -1; dz <= 1; dz += 1) {
        const bucket = grid.get(gridKey([cell[0] + dx, cell[1] + dy, cell[2] + dz]));
        if (!bucket) continue;
        for (const index of bucket) callback(index);
      }
    }
  }
}

function separatePair(
  point: [number, number, number],
  other: [number, number, number],
  index: number,
  otherIndex: number,
  minDistance: number,
  minDistanceSq: number,
): void {
  let dx = point[0] - other[0];
  let dy = point[1] - other[1];
  let dz = point[2] - other[2];
  let distanceSq = dx * dx + dy * dy + dz * dz;
  if (distanceSq >= minDistanceSq) return;

  if (distanceSq < 1e-9) {
    const direction = deterministicUnitVector(index, otherIndex);
    dx = direction[0];
    dy = direction[1];
    dz = direction[2];
    distanceSq = 1;
  }

  const distance = Math.sqrt(distanceSq);
  const push = ((minDistance - distance) * 0.5) / distance;
  const pushX = dx * push;
  const pushY = dy * push;
  const pushZ = dz * push;
  point[0] += pushX;
  point[1] += pushY;
  point[2] += pushZ;
  other[0] -= pushX;
  other[1] -= pushY;
  other[2] -= pushZ;
}

function deterministicUnitVector(index: number, otherIndex: number): Vec3 {
  const random = seededRandom((index + 1) * 65_537 + (otherIndex + 1) * 1_048_583);
  return randomUnitVector(random);
}

function randomUnitVector(random: () => number): Vec3 {
  const z = random() * 2 - 1;
  const theta = random() * Math.PI * 2;
  const radius = Math.sqrt(Math.max(0, 1 - z * z));
  return [Math.cos(theta) * radius, Math.sin(theta) * radius, z];
}

function hashString(value: string): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

function getBounds(points: readonly Vec3[]): { min: Vec3; max: Vec3 } {
  const first = points[0] ?? [0, 0, 0];
  const min: [number, number, number] = [first[0], first[1], first[2]];
  const max: [number, number, number] = [first[0], first[1], first[2]];

  for (const point of points) {
    min[0] = Math.min(min[0], point[0]);
    min[1] = Math.min(min[1], point[1]);
    min[2] = Math.min(min[2], point[2]);
    max[0] = Math.max(max[0], point[0]);
    max[1] = Math.max(max[1], point[1]);
    max[2] = Math.max(max[2], point[2]);
  }

  return { min, max };
}

function assertFiniteVector(vector: readonly number[], expectedLength: number | undefined, label: string): void {
  if (expectedLength !== undefined && vector.length !== expectedLength) {
    throw new Error(`${label} must have length ${expectedLength}; got ${vector.length}.`);
  }
  for (const [index, value] of vector.entries()) {
    if (!Number.isFinite(value)) {
      throw new Error(`${label} contains a non-finite value at index ${index}.`);
    }
  }
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4_294_967_296;
  };
}
