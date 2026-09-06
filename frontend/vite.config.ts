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
  },
})
