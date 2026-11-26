import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the API runs on its own port; /api calls go there.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:{{ApiPort}}',
    },
  },
});
