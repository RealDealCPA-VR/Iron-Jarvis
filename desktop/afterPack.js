// electron-builder afterPack hook.
//
// The Next.js standalone bundle's `node_modules` (which contains `next` and its
// runtime deps) is silently DROPPED by electron-builder's default extraResources
// copy — so the packaged `resources/dashboard/server.js` dies at launch with
// "Cannot find module 'next'". A `filter` doesn't override it. This hook runs after
// the app dir is packed but BEFORE the installer is built, and copies the traced
// node_modules into the packaged dashboard (dereferencing any symlinks) so the
// standalone server is self-contained.
const fs = require("fs");
const path = require("path");

const integrity = require("./integrity");

// A real bundle has thousands of files (frozen daemon alone is >1000). A count
// under this floor means extraResources silently dropped something big — fail
// the BUILD, not the user's boot.
const MANIFEST_FLOOR = 1000;

exports.default = async function afterPack(context) {
  // Local-module drift check: every `require("./x")` in the desktop sources
  // must be listed in build.files, or the packaged main process dies at its
  // require line before any window exists (v1.126.0 shipped without
  // integrity.js exactly this way). Fail the BUILD, not the user's launch.
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "package.json"), "utf8"));
  const bundled = new Set(pkg.build.files);
  for (const entry of ["main.js", "preload.js", "spotlight-preload.js"]) {
    const srcText = fs.readFileSync(path.join(__dirname, entry), "utf8");
    for (const m of srcText.matchAll(/require\(["']\.\/([\w-]+)["']\)/g)) {
      const dep = `${m[1]}.js`;
      if (!bundled.has(dep)) {
        throw new Error(`[afterPack] ${entry} requires ./${m[1]} but build.files omits ${dep} — the packaged app would crash at boot`);
      }
    }
  }

  const src = path.join(__dirname, "..", "dashboard", ".next", "standalone", "node_modules");
  const dst = path.join(context.appOutDir, "resources", "dashboard", "node_modules");
  if (!fs.existsSync(src)) {
    console.warn(`[afterPack] standalone node_modules missing (${src}) — did the dashboard build run?`);
    return;
  }
  fs.cpSync(src, dst, { recursive: true, dereference: true, force: true });
  const ok = fs.existsSync(path.join(dst, "next"));
  console.log(`[afterPack] staged dashboard node_modules -> ${dst} (next present: ${ok})`);
  if (!ok) throw new Error("[afterPack] node_modules/next did not land — dashboard would not boot");

  const resourcesDir = path.join(context.appOutDir, "resources");

  // The browser add-on (v1.239.0). extraResources copies extensions/chrome ->
  // resources/browser-addon, and that folder is what the Browser page tells the
  // user to point Chrome's "Load unpacked" at — so it must be COMPLETE, not
  // merely present.
  //
  // The failure this catches: extensions/chrome/dist is gitignored and produced
  // by extensions/chrome/scripts/build.mjs. If that build did not run (a fresh
  // clone, a `-SkipDashboard`-style shortcut, a CI step quietly reordered),
  // electron-builder still copies manifest.json — the filter matches it — and
  // ships an add-on folder with no service worker in it. Chrome's answer is
  // "Service worker registration failed. Status code: 15", which names no file,
  // on the user's machine, after an install. Fail the BUILD instead.
  const addonDir = path.join(resourcesDir, "browser-addon");
  for (const rel of ["manifest.json", path.join("dist", "background.js")]) {
    const abs = path.join(addonDir, rel);
    if (!fs.existsSync(abs) || fs.statSync(abs).size === 0) {
      throw new Error(`[afterPack] browser add-on incomplete: ${abs} is missing or empty — run extensions/chrome/scripts/build.mjs before packaging (build-installer.ps1 stage 3c)`);
    }
  }
  console.log(`[afterPack] browser add-on staged -> ${addonDir}`);

  // Inventory everything we just shipped so the packaged app can verify at boot
  // that the NSIS extraction actually completed (see integrity.js for the
  // v1.124.0 truncated-update incident this guards against).
  //
  // A BUNDLED DIRECTORY MISSING FROM THIS LIST IS NOT INVENTORIED AT ALL, and
  // therefore cannot fail the boot-time check — which is precisely the
  // half-installed-bundle failure integrity.js was written for. Adding a
  // `to:` target to build.extraResources means adding it here in the same
  // change; tests/test_browser_packaging_v1239.py pins the two lists together.
  const version = context.packager.appInfo.version;
  const manifest = integrity.buildManifest(resourcesDir, ["daemon", "dashboard", "vosk-model", "browser-addon"], version);
  const count = Object.keys(manifest.files).length;
  if (count < MANIFEST_FLOOR) {
    throw new Error(`[afterPack] install manifest has only ${count} files — the bundle is hollow`);
  }
  fs.writeFileSync(integrity.manifestPath(resourcesDir), JSON.stringify(manifest));
  console.log(`[afterPack] install manifest: ${count} files recorded (v${version})`);
};
