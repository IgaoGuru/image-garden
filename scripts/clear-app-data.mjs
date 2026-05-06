#!/usr/bin/env node
import { rm } from 'node:fs/promises';
import { homedir, platform } from 'node:os';
import path from 'node:path';

function defaultDataDir() {
  if (platform() === 'win32') {
    return path.join(
      process.env.LOCALAPPDATA || path.join(homedir(), 'AppData', 'Local'),
      'Constellation',
    );
  }
  if (platform() === 'darwin') {
    return path.join(homedir(), 'Library', 'Application Support', 'Constellation');
  }
  return path.join(
    process.env.XDG_DATA_HOME || path.join(homedir(), '.local', 'share'),
    'constellation',
  );
}

function parseDataDir(argv) {
  const dataDirIndex = argv.indexOf('--data-dir');
  if (dataDirIndex !== -1) {
    const value = argv[dataDirIndex + 1];
    if (!value) {
      throw new Error('--data-dir requires a path');
    }
    return value;
  }
  const inline = argv.find((arg) => arg.startsWith('--data-dir='));
  if (inline) {
    return inline.slice('--data-dir='.length);
  }
  return process.env.CONSTELLATION_DATA_DIR || defaultDataDir();
}

const dataDir = path.resolve(parseDataDir(process.argv.slice(2)));
await rm(dataDir, { recursive: true, force: true });
console.log(`Cleared Constellation app data: ${dataDir}`);
