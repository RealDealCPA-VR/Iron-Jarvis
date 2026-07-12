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
  },
});
