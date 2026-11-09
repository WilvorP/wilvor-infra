import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

function localDevProxyTarget(): string {
  const raw = process.env.WILVOR_LOCAL_DEV_COPILOT_ORIGIN ?? 'http://127.0.0.1:8765';
  const url = new URL(raw);

  if (
    url.protocol !== 'http:' ||
    (url.hostname !== '127.0.0.1' && url.hostname !== 'localhost')
  ) {
    throw new Error(
      'WILVOR_LOCAL_DEV_COPILOT_ORIGIN must be an http loopback origin.',
    );
  }

  return url.origin;
}

export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  server: {
    // The operational API Gateway CORS allowlist is exact-origin and contains
    // http://localhost:5173 (modules/operational_api/variables.tf). Failing
    // loudly on a busy port is better than silently drifting to 5174 and
    // producing opaque CORS errors against the real API.
    port: 5173,
    strictPort: true,
    proxy: {
      '/local-dev': {
        target: localDevProxyTarget(),
        changeOrigin: false,
      },
    },
  },

  preview: {
    port: 4173,
    strictPort: true,
  },

  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // MapLibre is by far the largest dependency. Splitting it out lets the
        // console shell and KPI strip parse and paint while the map library is
        // still downloading, and keeps it cached across application deploys.
        manualChunks: {
          maplibre: ['maplibre-gl'],
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },

  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
