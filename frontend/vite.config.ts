import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // 允许树莓派局域网内访问
    port: 5173,
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/zrender')) return 'vendor-zrender';
          if (id.includes('node_modules/echarts')) return 'vendor-echarts';
          // 显式列出 react / react-dom（T-26）：旧的 includes('node_modules/react')
          // 是子串匹配，把 react-dom 捎带进 vendor-react 属于无意行为，意图不明。
          if (/node_modules[\\/]react(-dom)?[\\/]/.test(id)) {
            return 'vendor-react';
          }
        },
      },
    },
  },
});
