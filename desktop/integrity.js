// Iron Jarvis — bundled-install integrity.
//
// WHY THIS EXISTS: the v1.124.0 auto-update once landed with
// resources/dashboard/node_modules cut off mid-extraction (the NSIS installer
// was interrupted — the downloaded installer itself was intact). The dashboard
// then crash-looped on "Cannot find module 'next'" for an hour with nothing
// telling the user the INSTALL was damaged, and recovery meant a manual
// reinstall. Existence-of-one-file checks can't catch this class of failure:
// today it was `next`, tomorrow it's an arbitrary file anywhere in the bundle.
//
// The fix is a full inventory: afterPack records every bundled file + its size
// into resources/install-manifest.json at build time; the packaged app verifies
// that inventory at boot BEFORE spawning anything, so a half-installed app
// repairs itself instead of crash-looping.
//
// Size + existence (no hashing): truncated extraction manifests as missing or
// short files, and hashing a multi-GB bundle on every boot is not acceptable.

const fs = require("fs");
const path = require("path");

const MANIFEST_NAME = "install-manifest.json";

// Runtime-mutable paths that must NOT be inventoried: Next's standalone server
// writes its ISR/image cache under .next/cache, so recording those files would
// make a healthy install verify as "damaged" later.
const EXCLUDE_RE = /(^|\/)\.next\/cache(\/|$)/;

function manifestPath(resourcesDir) {
  return path.join(resourcesDir, MANIFEST_NAME);
}

function walkFiles(dir, rel, files) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, entry.name);
    const key = rel ? `${rel}/${entry.name}` : entry.name;
    if (EXCLUDE_RE.test(key)) continue;
    if (entry.isDirectory()) {
      walkFiles(abs, key, files);
    } else if (entry.isFile()) {
      files[key] = fs.statSync(abs).size;
    }
  }
}

// Inventory the given subdirs of resourcesDir. Keys are resources-relative
// with forward slashes ("dashboard/node_modules/next/package.json").
function buildManifest(resourcesDir, subdirs, version) {
  const files = {};
  for (const sub of subdirs) {
    const dir = path.join(resourcesDir, sub);
    if (fs.existsSync(dir)) walkFiles(dir, sub, files);
  }
  return { version: version || null, files };
}

// Check every manifest entry against disk. Files that APPEARED since packing
// (runtime caches) are ignored — only recorded files can fail the check.
function verifyManifest(resourcesDir, manifest) {
  const missing = [];
  const mismatched = [];
  let checked = 0;
  const files = (manifest && manifest.files) || {};
  for (const [key, size] of Object.entries(files)) {
    checked += 1;
    let st;
    try {
      st = fs.statSync(path.join(resourcesDir, key));
    } catch {
      missing.push(key);
      continue;
    }
    if (typeof size === "number" && st.size !== size) mismatched.push(key);
  }
  return { ok: missing.length === 0 && mismatched.length === 0, checked, missing, mismatched };
}

module.exports = { MANIFEST_NAME, manifestPath, buildManifest, verifyManifest };
