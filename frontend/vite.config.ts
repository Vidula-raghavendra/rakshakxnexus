import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
      '/frames': 'http://localhost:8001',
      '/cameras': 'http://localhost:8001',
      '/stream': { target: 'ws://localhost:8001', ws: true },
    }
  }
})
