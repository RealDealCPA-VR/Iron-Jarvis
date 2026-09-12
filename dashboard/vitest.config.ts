import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Minimal dashboard test harness. @vitejs/plugin-react handles the JSX/TSX
// transform (the app's tsconfig sets `jsx: preserve` for Next, which the bare
// oxc transform would otherwise leave untransformed and fail to parse).
export default defineConfig({
  plugins: [react()],
  // Mirror tsconfig's `@/*` -> repo-root path alias so tests import the same way
  // the app does (e.g. `@/lib/api`, `@/components/ui`).
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["__tests__/**/*.test.{ts,tsx}"],
    setupFiles: ["./__tests__/setup.ts"],
    // THE 5 s DEFAULT IS AN ABSOLUTE WALL-CLOCK THRESHOLD, AND IT MEASURES THE
    // RUNNER (v1.254.2). Three gates in one release cycle died on it, each on a
    // DIFFERENT timing-sensitive test, and every one of them passed the same
    // commit in the Tests workflow while failing in Release. The reason is the
    // job layout, not the tests: Tests splits work across four jobs so its
    // vitest gets a runner to itself, while Release runs ONE `suite` job — the
    // add-on pnpm install, the add-on build, this suite, the node checks, and
    // the whole Python suite at -n auto. Same command, far less machine.
    //
    // The individual waits were real defects and were fixed as such (wait for
    // the thing you assert — CLAUDE.md). This raises the bound that turned
    // slowness into failure. It hides nothing: a test timeout cannot swallow an
    // assertion, so a wrong expectation still fails with its own message — the
    // distinction v1.236.2 got wrong by giving a `waitFor` the entire per-test
    // budget, which left it unable to report what it had been waiting for. The
    // cost is that a genuinely hung test now takes 15 s to say so.
    testTimeout: 15_000,
  },
});
