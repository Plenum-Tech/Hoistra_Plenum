import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev` serves on :5173 and proxies /backend/* to the running gateway
// (docker compose -f docker-compose.single-url.local.yml up → http://localhost:3000),
// so the browser stays same-origin and the backends' CORS lists never matter.
// Point elsewhere with VITE_DEV_PROXY_TARGET.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      '/backend': {
        target: process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:3000',
        changeOrigin: true,
        // The orchestrator streams its chain-of-thought over a WebSocket
        // (/backend/deep-agents/api/workflow/ws/:session), so upgrades must pass through.
        ws: true
      }
    }
  },
  build: {
    outDir: 'dist',
    sourcemap: mode !== 'production',
    chunkSizeWarningLimit: 900
  }
}));
