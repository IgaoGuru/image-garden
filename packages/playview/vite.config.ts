import { resolve } from 'node:path';

import { defineConfig } from 'vite';

export default defineConfig({
  resolve: {
    alias: {
      '@image-garden/viewer': resolve(__dirname, '../viewer/src/index.ts'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8766',
    },
  },
});
