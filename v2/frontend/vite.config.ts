import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发时经代理访问后端，保持同源：后端不开 CORS，并拒绝跨站的写请求与 WS。
// 代理不改 Host（changeOrigin 保持 false），后端据此判断同源。
const backend = process.env.EC_BACKEND ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
});
