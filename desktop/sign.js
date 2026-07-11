// Custom electron-builder Windows code-signing hook (referenced by
// build.win.sign in package.json). electron-builder calls exports.default for
// every artifact it wants signed (the app .exe, the NSIS installer, bundled
// .dll/.node, ...), passing a `configuration` whose `.path` is the file on disk.
//
// CONTRACT (why this file exists):
//   * When signing credentials ARE present in the environment, sign the file —
//     and let any signing FAILURE throw, so a misconfigured signing setup fails
//     the build loudly instead of silently shipping an unsigned installer.
//   * When credentials are ABSENT (the default for local dev builds and for CI
//     until the user adds the secrets), log a clear notice and RETURN without
//     error, so the build proceeds and produces a working *unsigned* installer.
//     Unsigned installers work fine; they just trip SmartScreen's "unknown
//     publisher" prompt. See docs/SIGNING.md.
//
// Two signing backends are supported, checked in this order:
//   1. Azure Trusted Signing (recommended, ~$10/mo, cloud HSM, CI-native):
//        AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET  (service principal)
//        IJ_SIGN_ENDPOINT   e.g. https://wus2.codesigning.azure.net/
//        IJ_SIGN_ACCOUNT    Trusted Signing account name
//        IJ_SIGN_PROFILE    certificate profile name
//      Signed via the `trusted-signing-cli` tool (install it on the runner:
//        cargo install trusted-signing-cli   — or ship it another way).
//   2. signtool / eSigner fallback (USB-token-cloud CAs, self-managed cert):
//        IJ_SIGNTOOL_PATH   full path to signtool.exe
//        IJ_SIGN_SHA1       SHA-1 thumbprint of the cert in the machine store
//      (eSigner users typically point IJ_SIGNTOOL_PATH at CodeSignTool's
//       signtool shim; adjust the args below to your CA's CLI if needed.)
//
// Timestamping (keeps signatures valid past cert expiry) is always applied.

"use strict";

const { execFileSync } = require("node:child_process");

const TIMESTAMP_URL =
  process.env.IJ_TIMESTAMP_URL || "http://timestamp.acs.microsoft.com";

function hasAzureTrustedSigning() {
  return Boolean(
    process.env.AZURE_TENANT_ID &&
      process.env.AZURE_CLIENT_ID &&
      process.env.AZURE_CLIENT_SECRET &&
      process.env.IJ_SIGN_ENDPOINT &&
      process.env.IJ_SIGN_ACCOUNT &&
      process.env.IJ_SIGN_PROFILE
  );
}

function hasSigntool() {
  return Boolean(process.env.IJ_SIGNTOOL_PATH && process.env.IJ_SIGN_SHA1);
}

function run(cmd, args) {
  // Inherit stdio so signing tool output is visible in the build log; throws a
  // non-zero-exit Error which we deliberately let propagate to fail the build.
  execFileSync(cmd, args, { stdio: "inherit" });
}

function signWithAzureTrustedSigning(file) {
  console.log(`  signing via Azure Trusted Signing: ${file}`);
  run("trusted-signing-cli", [
    "-e",
    process.env.IJ_SIGN_ENDPOINT,
    "-a",
    process.env.IJ_SIGN_ACCOUNT,
    "-c",
    process.env.IJ_SIGN_PROFILE,
    "-t",
    TIMESTAMP_URL,
    file,
  ]);
}

function signWithSigntool(file) {
  console.log(`  signing via signtool: ${file}`);
  run(process.env.IJ_SIGNTOOL_PATH, [
    "sign",
    "/fd",
    "SHA256",
    "/sha1",
    process.env.IJ_SIGN_SHA1,
    "/tr",
    TIMESTAMP_URL,
    "/td",
    "SHA256",
    file,
  ]);
}

exports.default = async function sign(configuration) {
  const file = configuration && configuration.path;
  if (!file) {
    // Nothing to sign — nothing to do.
    return;
  }

  if (hasAzureTrustedSigning()) {
    signWithAzureTrustedSigning(file);
    return;
  }

  if (hasSigntool()) {
    signWithSigntool(file);
    return;
  }

  console.log(
    `code-signing skipped (no cert configured) — installer will be unsigned: ${file}`
  );
  // Return WITHOUT throwing so electron-builder finishes the build unsigned.
};
