import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The Pi never builds this. `npm run build` runs on the Mac and the contents of
// dist/ are copied across, so there is deliberately no Pi-side build step
// anywhere in this project. See scripts/deploy.sh.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind to all interfaces so the dev server is reachable from the iPhone over
    // home Wi-Fi, which is the only way to judge this design honestly.
    host: true,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // The 3D bed on the home screen is its own chunk, about 760 kB before
    // compression and most of that Three.js. It is already loaded lazily, after
    // everything else, so the usual 500 kB warning is only noise on every build.
    chunkSizeWarningLimit: 800,
  },
})
