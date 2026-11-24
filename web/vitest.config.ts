import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The frontend test harness (Addendum 11 P30). Kept SEPARATE from vite.config.ts
// so the dev-server/build sections there stay untouched. globals are OFF — tests
// import { describe, it, expect, ... } from "vitest" explicitly, so tsconfig needs
// no "vitest/globals" types entry and stays as the app ships it.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Vitest's 5s default is too tight for THIS suite on a normal dev machine, and the
    // failure mode is the worst kind: 43 jsdom files run in parallel, so whichever
    // handful loses the CPU race times out, and the failing set CHANGES between runs.
    // A suite that reports different failures each time trains everyone to ignore it,
    // which is worse than having no suite. Measured 2026-08-20: at 5s, 4-7 files failed
    // per run and the set never repeated; at 20s, 196/196 passed repeatedly, including
    // with a dev server competing for the CPU. Nothing here legitimately takes seconds,
    // so this buys reliability without hiding a real hang — a genuinely stuck test
    // still fails, just 15s later.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
