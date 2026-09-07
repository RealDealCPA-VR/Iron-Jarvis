// Build the Iron Jarvis browser add-on: four TypeScript entry points plus the
// two HTML surfaces, all into dist/, which is what manifest.json points at.
//
// Why the HTML files are COPIED rather than left in src/: the manifest's
// `action.default_popup` and the setup page's `chrome.runtime.getURL` both name
// a single directory, and a popup that loads `../popup/popup.html` while its
// bundle lives in dist/ resolves to a blank white panel with no error anywhere
// the user can see it. One output directory removes that failure mode.
//
// Why no watch mode and no minifier: this bundle is read by a reviewer and by
// Chrome's own extension inspector, and a minified service worker turns every
// stack trace from the socket layer into noise. It is 20 KB of code.

import { build } from "esbuild";
import { copyFile, mkdir, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = dirname(fileURLToPath(import.meta.url));
const OUT = join(ROOT, "dist");

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
  popup: join(ROOT, "src/popup/popup.ts"),
  setup: join(ROOT, "src/setup/setup.ts"),
};

/** HTML copied verbatim beside its bundle. */
const HTML = [
  ["src/popup/popup.html", "popup.html"],
  ["src/setup/setup.html", "setup.html"],
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
    "built dist/background.js, dist/content.js, dist/popup.js, dist/setup.js + 2 html",
  );
}

await main();
