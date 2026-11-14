import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre v6 ships its web worker as a separate ES module; pre-bundling breaks the
  // worker in dev ("Worker failed to load"), so serve the package files as-is.
  optimizeDeps: { exclude: ['maplibre-gl'] },
})
