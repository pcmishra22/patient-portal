import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// In dev, /api is proxied to FastAPI so the browser sees a single origin (no CORS needed).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
});
