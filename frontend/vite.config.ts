import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 前端开发服务器跑在 5173，后端在 8000；跨域由后端 CORS 放行。
// 生产构建（dist）可由后端直接挂载。
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
})
