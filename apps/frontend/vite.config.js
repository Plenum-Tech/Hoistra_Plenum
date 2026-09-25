import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run dev` serves on :5173 and proxies /backend/* to the running gateway
// (docker compose -f docker-compose.single-url.local.yml up → http://localhost:3000),
// so the browser stays same-origin and the backends' CORS lists never matter.
// Point elsewhere with VITE_DEV_PROXY_TARGET — in the shell, or in a .env.<mode>.local
// (gitignored) read here for `vite --mode <mode>`, so a deployed backend can be the target
// of one named mode without touching .env.local's container ports.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return ({
  plugins: [react()],
  server: {
    port: Number(process.env.PORT) || 5173,
    // Binds every interface, not just localhost — harmless for a host-run `npm run dev`
    // (still reachable at localhost, now also on the LAN), and required for the
    // frontend-dev compose service: a container's own localhost is not what the host's
    // port mapping forwards to, and a bare `--host` omission there is a silently-empty
    // "connection refused" on the mapped port with no error in the Vite log at all.
    host: true,
    // The CHOKIDAR_USEPOLLING env var alone was not enough — Vite passes its own explicit
    // options into chokidar, which take precedence over whatever the env var would
    // otherwise set, so it went on doing native fs-events watching (which never sees a
    // macOS bind-mounted container's edits) regardless of the env var being present and
    // correct inside the container. Explicit here, still gated on that same env var, so a
    // host-run `npm run dev` is untouched — polling is strictly worse there, since native
    // events already work.
    watch: process.env.CHOKIDAR_USEPOLLING === 'true'
      ? { usePolling: true, interval: Number(process.env.CHOKIDAR_INTERVAL) || 300 }
      : undefined,
    proxy: {
      '/backend': {
        target: process.env.VITE_DEV_PROXY_TARGET || env.VITE_DEV_PROXY_TARGET || 'http://localhost:3000',
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
  });
});
