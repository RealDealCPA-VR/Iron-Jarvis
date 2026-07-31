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

  // Inventory everything we just shipped so the packaged app can verify at boot
  // that the NSIS extraction actually completed (see integrity.js for the
  // v1.124.0 truncated-update incident this guards against).
  const resourcesDir = path.join(context.appOutDir, "resources");
  const version = context.packager.appInfo.version;
  const manifest = integrity.buildManifest(resourcesDir, ["daemon", "dashboard", "vosk-model"], version);
  const count = Object.keys(manifest.files).length;
  if (count < MANIFEST_FLOOR) {
    throw new Error(`[afterPack] install manifest has only ${count} files — the bundle is hollow`);
  }
  fs.writeFileSync(integrity.manifestPath(resourcesDir), JSON.stringify(manifest));
  console.log(`[afterPack] install manifest: ${count} files recorded (v${version})`);
};
