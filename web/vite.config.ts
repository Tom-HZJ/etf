import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { readFileSync } from 'node:fs';

const backendConfig = readFileSync(new URL('../server/etfmon/config.py', import.meta.url), 'utf8');
const releaseVersion = /^APP_VERSION\s*=\s*"([^"\n]+)"/m.exec(backendConfig)?.[1];
if (!releaseVersion) throw new Error('Missing APP_VERSION in server/etfmon/config.py');

// Dev server proxies /api to the local backend; production build is served by nginx.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __ETF_RELEASE_VERSION__: JSON.stringify(releaseVersion) },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.ETF_API_DEV ?? 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      ...(process.env.ETF_VERIFY_REPLAY === '1' ? { input: { app:'index.html', replay:'verify-replay.html' } } : {}),
      output: {
        // Charts are only reachable through lazy routes; keep them out of the
        // first-screen payload. React/Astryx are shared by the shell.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;
          if (id.includes('lightweight-charts')) return 'vendor-charts';
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/scheduler/') ||
            id.includes('\\react\\') ||
            id.includes('\\scheduler\\')
          ) {
            return 'vendor-react';
          }
          return undefined;
        },
      },
    },
  },
  // `npm run preview` serves the production dist locally; proxy /api so the
  // built bundle can be verified against the real backend before deploy.
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: process.env.ETF_API_DEV ?? 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
});
