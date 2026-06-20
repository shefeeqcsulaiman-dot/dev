import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'pos-route-rewrite',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (req.url === '/pos' || req.url?.startsWith('/pos?')) {
            req.url = '/taxflow/pos.html'
          }
          next()
        })
      }
    }
  ]
})
