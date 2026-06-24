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
          } else if (req.url === '/login' || req.url?.startsWith('/login?')) {
            req.url = '/taxflow/login.html'
          } else if (req.url === '/app' || req.url?.startsWith('/app?')) {
            req.url = '/taxflow/index.html'
          }
          next()
        })
      }
    }
  ]
})
