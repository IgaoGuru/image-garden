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
    return images.map((image, index) => ({ ...image, position: transformed[index] ?? [0, 0, 0] }));
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
  return images.map((image, index) => ({ ...image, position: transformed[index] ?? [0, 0, 0] }));
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
