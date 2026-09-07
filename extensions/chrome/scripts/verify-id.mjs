// Verify that the extension id Chrome will compute matches the one the daemon pins.
//
// Run by `pnpm run verify-id`, by `pnpm run check`, and by the release workflow. Its
// pytest mirror is tests/test_browser_extension_id_v1235.py, so drift fails a gate
// on both sides of the language boundary.
//
// THE SILENT FAILURE THIS CATCHES, precisely. The daemon's origin guard admits
// exactly `chrome-extension://<pinned id>` at `/browser/ws` and nothing else. If
// manifest.json's `key` and identity.py's PINNED_EXTENSION_ID ever disagree, the real
// add-on's origin is refused with WebSocket close 1008, the card sits on
// "Waiting to pair" forever, and nothing in either log names an id mismatch. It looks
// like a networking problem and it is a one-character problem.
//
// Chrome's rule, implemented once here and once in
// src/iron_jarvis/browser/identity.py::extension_id_from_spki_b64: base64-decode the
// SPKI in `key`, SHA-256 it, take the first 16 bytes as hex, and map each hex digit
// `0-9a-f` onto `a-p`.
//
// Three separate checks, because each fails differently:
//   1. The manifest `key` is one line with no whitespace. Chrome's own instructions
//      are to remove the newlines; a key with a stray `\n` is silently rejected and
//      Chrome falls back to a DIRECTORY-DERIVED id, which differs per machine —
//      exactly the developer-machine-state dependency D27A exists to remove.
//   2. The manifest `key` equals identity.py's PINNED_EXTENSION_KEY. Two keys that
//      each derive a valid id would pass check 3 on one file and fail in the browser.
//   3. The derived id equals identity.py's PINNED_EXTENSION_ID.
//
// Exits 0 on success and 1 with a message naming which check failed. It never
// rewrites either file: a verifier that repaired the drift would hide the fact that
// the two halves had parted.

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const MANIFEST = join(HERE, "..", "manifest.json");
const IDENTITY = resolve(HERE, "..", "..", "..", "src", "iron_jarvis", "browser", "identity.py");

const ID_LENGTH = 32;
const ID_ALPHABET = "abcdefghijklmnop";

/** Chrome's derivation. The one implementation on this side of the boundary. */
export function extensionIdFromSpkiB64(spkiB64) {
  const body = spkiB64
    .trim()
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => !line.startsWith("-----"))
    .join("");
  if (!body) {
    throw new Error("empty public key: nothing to derive an extension id from");
  }
  const der = Buffer.from(body, "base64");
  if (der.length === 0) {
    throw new Error("public key is not valid base64");
  }
  const digest = createHash("sha256").update(der).digest("hex").slice(0, ID_LENGTH);
  return [...digest].map((ch) => ID_ALPHABET[parseInt(ch, 16)]).join("");
}

/**
 * Read a text file with CRLF normalised at the reader.
 *
 * Both files are checked out with native line endings on Windows, and a pin that
 * matched only on LF would fail on the machine this repository is actually developed
 * on. Normalising here — not in the patterns below — keeps every pattern free of an
 * embedded newline.
 */
async function readText(path) {
  const raw = await readFile(path, "utf8");
  return raw.replace(/\r\n/g, "\n");
}

/** The Python-side constants, read out of identity.py rather than duplicated. */
function pinnedFromIdentity(source) {
  const keyBlock = /PINNED_EXTENSION_KEY = \(([^)]*)\)/.exec(source);
  if (!keyBlock) {
    throw new Error(`could not find PINNED_EXTENSION_KEY in ${IDENTITY}`);
  }
  const key = [...keyBlock[1].matchAll(/"([^"]*)"/g)].map((m) => m[1]).join("");
  const idMatch = /PINNED_EXTENSION_ID = "([a-p]{32})"/.exec(source);
  if (!idMatch) {
    throw new Error(`could not find PINNED_EXTENSION_ID in ${IDENTITY}`);
  }
  return { key, id: idMatch[1] };
}

async function main() {
  const manifestText = await readText(MANIFEST);
  const manifest = JSON.parse(manifestText);
  const key = manifest.key;
  const failures = [];

  if (typeof key !== "string" || key.length === 0) {
    failures.push(`manifest.json has no "key" field, so Chrome would derive the id from the\n  unpacked directory path and it would differ on every machine.`);
    report(failures);
    return;
  }
  if (/\s/.test(key)) {
    failures.push(`manifest.json "key" contains whitespace. Chrome requires the base64 body as a\n  single line; a newline makes it fall back to a directory-derived id.`);
  }

  const pinned = pinnedFromIdentity(await readText(IDENTITY));
  if (key !== pinned.key) {
    failures.push(`manifest.json "key" differs from identity.PINNED_EXTENSION_KEY.\n  manifest: ${preview(key)}\n  identity: ${preview(pinned.key)}`);
  }

  const derived = extensionIdFromSpkiB64(key);
  if (derived !== pinned.id) {
    failures.push(`the id Chrome derives from manifest.json "key" is not the pinned id, so the\n  daemon's origin guard would refuse the real add-on with close 1008.\n  derived: ${derived}\n  pinned:  ${pinned.id}`);
  }

  if (failures.length === 0) {
    console.log(`verify-id: OK — manifest key derives ${derived}, matching identity.PINNED_EXTENSION_ID`);
  }
  report(failures);
}

function preview(text) {
  return text.length > 24 ? `${text.slice(0, 24)}… (${text.length} chars)` : text;
}

function report(failures) {
  if (failures.length === 0) {
    return;
  }
  console.error("verify-id FAILED:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exitCode = 1;
}

await main();
