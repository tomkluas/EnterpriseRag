import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

// 开发服务器转发 API 到本机后端，生产静态资源由后端镜像提供。
export default defineConfig({
  plugins: [vue()],
  // 统一以 src 为别名根目录，避免组件层级改变时改写相对引用。
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18000',
        changeOrigin: true,
      }
    }
  }
})
