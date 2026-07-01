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

exports.default = async function afterPack(context) {
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
};
