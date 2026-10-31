/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = process.env.TRACEFORGE_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "localhost",
    port: 5173,
    strictPort: true,
    // Same-origin API calls: the session cookie and CSRF token work without cross-site cookies.
    proxy: { "/api": { target: backend, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
