// Build the Iron Jarvis browser add-on: four TypeScript entry points plus the
// two HTML surfaces, all into dist/, which is what manifest.json points at.
//
// Why the HTML files are COPIED rather than left in src/: the manifest's
// `side_panel.default_path` and the setup page's `chrome.runtime.getURL` both name
// a single directory, and a page that loads `../sidepanel/sidepanel.html` while its
// bundle lives in dist/ resolves to a blank white panel with no error anywhere
// the user can see it. One output directory removes that failure mode.
//
// Why no watch mode and no minifier: this bundle is read by a reviewer and by
// Chrome's own extension inspector, and a minified service worker turns every
// stack trace from the socket layer into noise. It is 20 KB of code.

import { build } from "esbuild";
import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
// DEV-ONLY OVERRIDES (v1.259.0). Production builds set none of these and get
// exactly the addresses below.
//   IJ_ADDON_OUT        write the bundle somewhere other than ./dist, so a
//                       verification build never overwrites the committed
//                       layout's dist/.
//   IJ_ADDON_DAEMON_WS  the daemon socket the bundle dials. LOOPBACK ONLY: anything
//                       but ws://127.0.0.1:<port>/browser/ws is refused here, so
//                       the bridge stays local by construction even in a developer
//                       build. It exists so the add-on can be proven against an
//                       ISOLATED daemon on another port — a build that dials 8787,
//                       loaded into a second browser, would REPLACE the user's real
//                       pairing (a newer connection replaces the older).
//   IJ_ADDON_JARVIS_URL the dashboard the setup page links to; same rule.
const OUT = process.env.IJ_ADDON_OUT ? resolve(process.env.IJ_ADDON_OUT) : join(ROOT, "dist");
const DEFAULT_DAEMON_WS = "ws://127.0.0.1:8787/browser/ws";
const DEFAULT_JARVIS_URL = "http://127.0.0.1:8788/computeruse";

/** Return `value` only if it matches the loopback shape; refuse loudly otherwise. */
function loopbackOnly(name, value, fallback, pattern) {
  if (!value) return fallback;
  if (!pattern.test(value)) {
    console.error(`${name} must be a loopback address matching ${pattern}; refusing "${value}"`);
    process.exit(1);
  }
  return value;
}
const DAEMON_WS = loopbackOnly(
  "IJ_ADDON_DAEMON_WS",
  process.env.IJ_ADDON_DAEMON_WS,
  DEFAULT_DAEMON_WS,
  /^ws:\/\/127\.0\.0\.1:\d{2,5}\/browser\/ws$/,
);
const JARVIS_URL = loopbackOnly(
  "IJ_ADDON_JARVIS_URL",
  process.env.IJ_ADDON_JARVIS_URL,
  DEFAULT_JARVIS_URL,
  /^http:\/\/127\.0\.0\.1:\d{2,5}\/computeruse$/,
);
/** Compile-time constants the bundles read (see socket.ts and background/index.ts). */
const DEFINES = {
  __IJ_DAEMON_WS__: JSON.stringify(DAEMON_WS),
  __IJ_JARVIS_URL__: JSON.stringify(JARVIS_URL),
};

/** Output name -> entry point, one per surface.
 *
 * The map form is deliberate: it names `dist/background.js` directly, which is
 * the filename manifest.json declares. Letting esbuild name the output after its
 * entry file would produce `dist/index.js`, and a manifest pointing at a missing
 * service worker fails with Chrome's "Status code: 15", which names no file.
 *
 * `content` is the same arrangement for the same reason, one level less obvious:
 * nothing in manifest.json points at it, because there is no `content_scripts`
 * block and there must not be one (plan section 6). It is named in ONE place,
 * `tabs.CONTENT_SCRIPT_FILE`, which is what `chrome.scripting.executeScript`
 * injects — and a rename here without a rename there fails at run time with
 * "Could not load file", inside a page, where nobody is watching a console. */
const ENTRY_POINTS = {
  background: join(ROOT, "src/background/index.ts"),
  content: join(ROOT, "src/content/index.ts"),
  sidepanel: join(ROOT, "src/sidepanel/sidepanel.ts"),
  setup: join(ROOT, "src/setup/setup.ts"),
  // v1.272.0: the microphone grant page — see src/mic/mic.html for why a page.
  mic: join(ROOT, "src/mic/mic.ts"),
};

/** HTML copied verbatim beside its bundle. */
const HTML = [
  ["src/sidepanel/sidepanel.html", "sidepanel.html"],
  ["src/setup/setup.html", "setup.html"],
  ["src/mic/mic.html", "mic.html"],
];

async function main() {
  // A stale dist/ is worse than no dist/: Chrome would keep loading the old
  // service worker while the source read as fixed.
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  const result = await build({
    entryPoints: ENTRY_POINTS,
    outdir: OUT,
    bundle: true,
    // IIFE, not ESM: an IIFE bundle is valid in a module service worker AND in a
    // classic page script, so neither surface can break on how the other is
    // loaded. Nothing here has a top-level await.
    format: "iife",
    // Chrome 120 is manifest.json's `minimum_chrome_version`. The two must
    // agree or the bundle uses syntax the minimum supported browser rejects at
    // parse time, which surfaces as a service worker that never registers.
    target: ["chrome120"],
    platform: "browser",
    define: DEFINES,
    sourcemap: "linked",
    logLevel: "info",
    legalComments: "none",
  });
  if (result.errors.length) {
    process.exitCode = 1;
    return;
  }

  for (const [from, to] of HTML) {
    await copyFile(join(ROOT, from), join(OUT, to));
  }
  console.log(
    `built ${OUT}: background.js, content.js, sidepanel.js, setup.js + 2 html` +
      (DAEMON_WS === DEFAULT_DAEMON_WS ? "" : ` (DEV daemon ${DAEMON_WS})`),
  );
}

await main();
