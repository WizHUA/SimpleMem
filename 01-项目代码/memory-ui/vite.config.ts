import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.MEMORY_UI_PROXY_TARGET || "http://127.0.0.1:8088";
const proxy = { "/api": backend, "/health": backend };

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: { proxy },
});
