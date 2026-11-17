import { defineConfig } from 'vite';

export default defineConfig({
  root: 'frontend',
  base: '/static/3d/',
  build: { outDir: '../static/3d', emptyOutDir: true },
  server: {
    proxy: Object.fromEntries(['/api', '/audio', '/video', '/static/models',
      '/static/gift', '/static/vendor'].map(path => [path, 'http://127.0.0.1:8000'])),
  },
});
