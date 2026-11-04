import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The page lives in web/ and builds into dist/, which crm/server.py serves. dist/ is committed,
// so running the CRM needs Python only. `npm run dev` proxies the API to a running server.
export default defineConfig({
  root: 'web',
  plugins: [react()],
  build: { outDir: '../dist', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:4320', changeOrigin: true } } },
});
