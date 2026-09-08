// Build the Iron Jarvis browser add-on the way a RELEASE builds it, and refuse to
// finish unless the folder it produced is one Chrome would actually load.
//
// WHY THIS FILE EXISTS AT ALL, given that `pnpm run check` already chains the same
// three commands. `dist/` is gitignored (extensions/chrome/.gitignore), so the folder
// a user points Chrome's "Load unpacked" at contains a manifest.json naming
// `dist/background.js` and NOTHING ELSE unless somebody ran a build first. Chrome's
// response to that is "Service worker registration failed. Status code: 15", which
// names no file, and the add-on never speaks — so the Your browser card sits on
// "Waiting to pair" and every log on both sides is silent. Making that impossible is
// the whole job: the installer bundles this folder (desktop/package.json
// `extraResources` -> `browser-addon`), so the build must run in the release, in CI,
// and in a LOCAL `build-installer.ps1` run, from ONE entry point that all three call.
//
// WHY IT VERIFIES INSTEAD OF TRUSTING esbuild's EXIT CODE. esbuild exits 0 when it
// wrote every entry point it was given. It has no idea which files manifest.json
// names, and it never reads tabs.ts's `CONTENT_SCRIPT_FILE`. A renamed entry point
// therefore builds clean and fails only inside a browser, inside a page, where nobody
// is watching a console. So the last stage re-reads the manifest and the two source
// constants that name a bundle by string, and checks each named file exists and is
// non-empty in dist/.
//
// Empty, not merely present: an interrupted copy leaves a zero-byte background.js,
// and a zero-byte service worker registers fine and does nothing at all.
//
// Stages, each fatal, in this order and for this reason:
//   1. verify-id  — the pinned extension id. Runs FIRST because a bundle built from a
//                   manifest whose `key` has drifted from identity.PINNED_EXTENSION_ID
//                   is refused by the daemon's origin guard with close 1008 no matter
//                   how good the code inside it is. Cheap, and it fails honestly.
//   2. typecheck  — `tsc --noEmit`. esbuild STRIPS types without checking them, so
//                   this is the only stage that can fail on a type error at all.
//   3. build      — esbuild.config.mjs, the one bundler configuration.
//   4. verify     — the manifest-vs-dist check described above.
//
// Exit 0 with a one-line summary, or exit 1 naming the stage and what it means. It
// writes nothing outside dist/ and it never repairs a mismatch: a wrapper that fixed
// a renamed bundle would hide that the manifest and the source had parted.

import { spawnSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ADDON = resolve(HERE, "..");
const DIST = join(ADDON, "dist");
const MANIFEST = join(ADDON, "manifest.json");

/** TypeScript's own entry point, run through THIS node.
 *
 * Not `pnpm exec tsc`, and not `node_modules/.bin/tsc`: the .bin entry on Windows is
 * a `.CMD` shim that `spawnSync` cannot execute without a shell, and shelling out
 * re-introduces PATH as a variable in a build whose entire point is determinism. The
 * real script is a plain JS file and node runs it directly. */
const TSC = join(ADDON, "node_modules", "typescript", "bin", "tsc");

/** Read a text file with CRLF normalised AT THE READER.
 *
 * This repository is developed on Windows and checked out with native line endings,
 * so a pattern below that carried `\n` would match on CI and not on the machine the
 * add-on is actually built on. Normalising here keeps every pattern newline-free. */
function readText(path) {
  return readFileSync(path, "utf8").replace(/\r\n/g, "\n");
}

function run(label, args, explain) {
  process.stdout.write(`addon build: ${label}...\n`);
  const result = spawnSync(process.execPath, args, { cwd: ADDON, stdio: "inherit" });
  if (result.error) {
    fail(label, `${result.error.message}\n  ${explain}`);
  }
  if (result.status !== 0) {
    fail(label, `exited ${result.status}.\n  ${explain}`);
  }
}

function fail(label, message) {
  console.error(`\nadd-on build FAILED at stage "${label}": ${message}`);
  process.exit(1);
}

/**
 * Every file the shipped add-on names by string, and where the name is written.
 *
 * Derived, never listed: manifest.json names the service worker and the side panel,
 * and
 * the background sources name the content script and the setup page (there is no
 * `content_scripts` block by design — plan section 6 — so the content bundle is
 * reachable only through `chrome.scripting.executeScript`). Reading the names from
 * their real homes means a rename on either side of the pair is caught here rather
 * than at run time.
 */
function requiredFiles() {
  const manifest = JSON.parse(readText(MANIFEST));
  const named = [];

  const worker = manifest.background && manifest.background.service_worker;
  if (typeof worker !== "string" || !worker) {
    fail("verify", `manifest.json declares no background.service_worker, so Chrome has\n  no entry point to register and the add-on can never connect.`);
  }
  named.push([worker, "manifest.json background.service_worker"]);

  // The SIDE PANEL, not a popup. `action.default_popup` was removed in v1.242.0
  // because Chrome ignores `setPanelBehavior({openPanelOnActionClick: true})` while
  // it is set (D32), and a verifier still reading the old field would find nothing,
  // check nothing, and let a build ship with no panel bundle at all -- the silent
  // half of this file's whole reason to exist. So it is REQUIRED, not optional.
  const panel = manifest.side_panel && manifest.side_panel.default_path;
  if (typeof panel !== "string" || !panel) {
    fail("verify", `manifest.json declares no side_panel.default_path, so the action click\n  opens nothing and the add-on has no chat surface at all.`);
  }
  named.push([panel, "manifest.json side_panel.default_path"]);

  for (const [source, pattern, where] of [
    ["src/background/tabs.ts", /CONTENT_SCRIPT_FILE = "([^"]+)"/, "tabs.ts CONTENT_SCRIPT_FILE"],
    ["src/background/hostperms.ts", /SETUP_PAGE = "([^"]+)"/, "hostperms.ts SETUP_PAGE"],
  ]) {
    const match = pattern.exec(readText(join(ADDON, source)));
    if (!match) {
      fail("verify", `could not find ${where} in ${source} — this wrapper can no longer tell\n  which bundle that surface loads, so it cannot promise the folder is loadable.`);
    }
    named.push([match[1], where]);
  }

  // Each HTML surface loads its own bundle; the page is useless without it.
  for (const [file, where] of [...named]) {
    if (file.endsWith(".html")) {
      named.push([file.replace(/\.html$/, ".js"), `${where} (its script)`]);
    }
  }
  return named;
}

function verifyDist() {
  process.stdout.write("addon build: verify...\n");
  const problems = [];
  const seen = new Set();
  for (const [file, where] of requiredFiles()) {
    if (seen.has(file)) continue;
    seen.add(file);
    let size;
    try {
      size = statSync(join(ADDON, file)).size;
    } catch {
      problems.push(`${file} is missing (named by ${where})`);
      continue;
    }
    if (size === 0) {
      problems.push(`${file} is zero bytes (named by ${where})`);
    }
  }
  if (problems.length) {
    fail(
      "verify",
      `the built add-on folder is not loadable:\n  - ${problems.join("\n  - ")}\n  Chrome would refuse it with "Service worker registration failed", naming no file.`,
    );
  }
  return seen.size;
}

run(
  "verify-id",
  [join(HERE, "verify-id.mjs")],
  `manifest.json "key" and identity.PINNED_EXTENSION_ID must agree, or the daemon's\n  origin guard refuses the real add-on with close 1008 and nothing names the mismatch.`,
);
run(
  "typecheck",
  [TSC, "--noEmit"],
  `esbuild strips types without checking them, so this is the only stage a type error\n  can fail in. Run "pnpm install" in extensions/chrome if TypeScript is not present.`,
);
run(
  "build",
  [join(ADDON, "esbuild.config.mjs")],
  `esbuild could not bundle the add-on. Run "pnpm install" in extensions/chrome first.`,
);
const count = verifyDist();
console.log(`add-on build: OK — ${count} named files present in ${DIST}`);
