import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 중에는 /api 를 Java 백엔드로 넘긴다. 브라우저 입장에서는 같은 출처가 되어
// 교차 출처 문제가 생기지 않고, 화면 코드에 백엔드 주소가 박히지도 않는다.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})
