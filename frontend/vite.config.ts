import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'robots.txt'],
      manifest: {
        name: 'ChronoKG — Cercetare Cunoaștere Cronologică',
        short_name: 'ChronoKG',
        description: 'Academic research workspace & chronological knowledge graph',
        theme_color: '#2f5fa8',
        background_color: '#f1f4f8',
        display: 'standalone',
        scope: '/',
        start_url: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' }
        ]
      },
      workbox: {
        // `mjs` matters: the pdf.js worker is an .mjs module. Without it the
        // reader breaks after every app update (the old worker hash 404s) and
        // it cannot open PDFs offline — the exact "PDF won't load on phone" bug.
        globPatterns: ['**/*.{js,mjs,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            urlPattern: /\/api\/studies\/(\d+)\/pdf/,
            handler: 'CacheFirst',
            options: {
              cacheName: 'study-pdfs',
              expiration: { maxEntries: 100, maxAgeSeconds: 60 * 60 * 24 * 30 },
              cacheableResponse: { statuses: [0, 200] },
              plugins: [
                {
                  cacheWillUpdate: async ({ response }) => {
                    if (response.status === 200 && response.headers.get('content-type')?.includes('pdf')) {
                      return response
                    }
                    return null
                  }
                }
              ]
            }
          },
          {
            urlPattern: /\/api\/studies\/(\d+)\/(notes|highlights)/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'study-annotations',
              networkTimeoutSeconds: 5,
              expiration: { maxEntries: 500, maxAgeSeconds: 60 * 60 * 24 * 7 },
              cacheableResponse: { statuses: [0, 200] }
            }
          },
          {
            urlPattern: /\/api\/.*/,
            handler: 'NetworkOnly'
          }
        ]
      }
    })
  ],
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
        manualChunks: {
          react: ['react', 'react-dom'],
          vis: ['vis-timeline', 'vis-network'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
})
