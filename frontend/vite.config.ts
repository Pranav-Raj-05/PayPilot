import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: { port: 5173 },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], css: false, restoreMocks: true },
  build: {
    sourcemap: false,
    rollupOptions: { output: { manualChunks: { charts: ["recharts"], vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"], supabase: ["@supabase/supabase-js"] } } },
  },
});
