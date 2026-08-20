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
  },
});
