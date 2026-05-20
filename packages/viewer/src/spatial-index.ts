import { Vector3 } from 'three';

export interface SpatialIndexedRecord {
  position: Vector3;
}

export class SpatialIndex<T extends SpatialIndexedRecord> {
  private readonly cells = new Map<string, T[]>();
  private readonly inverseCellSize: number;

  constructor(records: Iterable<T>, cellSize: number) {
    this.inverseCellSize = 1 / Math.max(1, cellSize);
    for (const record of records) this.insert(record);
  }

  queryRadius(center: Vector3, radius: number): T[] {
    const minX = this.cellCoord(center.x - radius);
    const maxX = this.cellCoord(center.x + radius);
    const minY = this.cellCoord(center.y - radius);
    const maxY = this.cellCoord(center.y + radius);
    const minZ = this.cellCoord(center.z - radius);
    const maxZ = this.cellCoord(center.z + radius);
    const results: T[] = [];

    for (let x = minX; x <= maxX; x += 1) {
      for (let y = minY; y <= maxY; y += 1) {
        for (let z = minZ; z <= maxZ; z += 1) {
          const records = this.cells.get(cellKey(x, y, z));
          if (records) results.push(...records);
        }
      }
    }

    return results;
  }

  private insert(record: T): void {
    const x = this.cellCoord(record.position.x);
    const y = this.cellCoord(record.position.y);
    const z = this.cellCoord(record.position.z);
    const key = cellKey(x, y, z);
    const records = this.cells.get(key);
    if (records) {
      records.push(record);
    } else {
      this.cells.set(key, [record]);
    }
  }

  private cellCoord(value: number): number {
    return Math.floor(value * this.inverseCellSize);
  }
}

export function spatialCellSize(radius: number): number {
  return Math.max(64, radius * 0.5);
}

function cellKey(x: number, y: number, z: number): string {
  return `${x},${y},${z}`;
}
