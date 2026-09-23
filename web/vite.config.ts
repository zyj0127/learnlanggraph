import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// VITE_PROXY_TARGET 可在 .env 中覆盖；默认代理到本地 FastAPI
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_PROXY_TARGET || 'http://localhost:8000'
  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
