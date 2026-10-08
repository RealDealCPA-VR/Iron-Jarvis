import { defineConfig } from "@playwright/test";

/**
 * End-to-end acceptance tests for the calm UI redesign (S12, AUDIT Q13):
 * T1 home minimalism, T2 reachability, T3 a fresh profile, and the 390 px
 * no-pan probe — in a real browser (the Edge this PC has; no download),
 * against a scratch daemon with a FRESH home whose model is the offline mock.
 *
 * Ports 8807/8808 so it never touches the live app (8787/8788) or the
 * everyday scratch stack (8797/8798). See e2e/README.md.
 */
const DAEMON = 8807;
const WEB = 8808;

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB}`,
    channel: process.env.IJ_E2E_CHANNEL || "msedge",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // A fresh, empty home every run: T3 is "an empty config directory".
      command: `node e2e/fresh-home.mjs && uv run --no-sync ironjarvis serve --root e2e/.home --port ${DAEMON}`,
      url: `http://127.0.0.1:${DAEMON}/health`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: { DOCKER_HOST: "tcp://127.0.0.1:1" },
    },
    {
      command: `npx next dev -p ${WEB}`,
      url: `http://127.0.0.1:${WEB}/everything`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: { NEXT_PUBLIC_IJ_API: `http://127.0.0.1:${DAEMON}`, IJ_NEXT_DIST_DIR: ".next-e2e" },
    },
  ],
});
