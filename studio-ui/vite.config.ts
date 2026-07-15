/// <reference types="vitest/config" />

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    exclude: ["e2e/**", "node_modules/**"],
    // Keep the suite deterministic and, more importantly, make the runner own
    // one long-lived worker instead of leaving a fork per test file behind on
    // Windows. This is also the execution model used by CI.
    pool: "threads",
    fileParallelism: false,
    maxWorkers: 1,
    minWorkers: 1,
    poolOptions: {
      threads: {
        singleThread: true,
      },
    },
  },
  base: "/",
  build: {
    outDir: "../src/ledgerline/data/studio",
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/media": "http://127.0.0.1:8765",
    },
  },
});
