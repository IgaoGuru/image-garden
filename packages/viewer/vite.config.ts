import { resolve } from 'node:path';

import { defineConfig } from 'vite';
import dts from 'vite-plugin-dts';

export default defineConfig({
  plugins: [
    dts({
      include: ['src'],
      entryRoot: 'src',
      insertTypesEntry: true,
      rollupTypes: true,
    }),
  ],
  build: {
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      name: 'ConstellationViewer',
      fileName: 'constellation-viewer',
      formats: ['es', 'umd'],
    },
    // Bundle runtime dependencies so Studio can serve `/viewer/constellation-viewer.js`
    // directly in a plain browser without a bundler/import-map. The package still
    // exposes normal ESM/UMD library entry points for npm consumers.
    sourcemap: true,
  },
  server: {
    open: '/demo/',
  },
});
