import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const api = process.env.SAHAY_API || 'http://localhost:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(['/v1', '/media', '/public', '/widget.js', '/w/', '/r/', '/webhooks', '/api', '/docs', '/api-docs', '/openapi.json'].map((p) => [p, api])),
  },
})
