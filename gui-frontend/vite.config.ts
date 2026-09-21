import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the UI runs on :5173 and the API on :8100. Proxying /api keeps them
// same-origin, which is also how it is deployed behind the reverse proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8100" },
  },
  build: { sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
