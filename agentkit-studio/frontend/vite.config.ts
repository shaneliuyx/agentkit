import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173; proxy /api → FastAPI backend so the EventSource and
// fetch helpers can use same-origin relative paths.
// Backend defaults to :8770 — NOT :8000, which is occupied by oMLX (the model
// server). Override with VITE_BACKEND_URL if the backend runs elsewhere.
// The backend routes are UNPREFIXED (/session, /backends, …), so the proxy must
// STRIP the /api prefix before forwarding — else /api/session 404s.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_BACKEND_URL ?? "http://localhost:8770",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
