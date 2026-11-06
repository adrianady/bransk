import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the heavy vendors so app-code changes do not invalidate the
        // whole framework bundle and the initial payload stays cacheable.
        manualChunks: {
          react: ['react', 'react-dom'],
          vis: ['vis-timeline', 'vis-network'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
})
