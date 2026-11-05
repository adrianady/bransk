import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Unit tests for the parts where a mistake is silent in the UI: the API client
// (error text, pagination, auth header) and the small pure helpers. Run with
// `npm test`. The app itself is still verified visually/build-wise.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
