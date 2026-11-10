// electron-builder config for a SIGNED Windows build (Microsoft Artifact
// Signing, formerly Azure Trusted Signing).
//
// It is package.json's own "build" block plus `win.azureSignOptions`, so there
// is ONE build config and signing only adds to it. build-installer.ps1 passes
// `--config builder-signed.js` ONLY when all six signing values are in the
// environment; every other build (local, or CI before the secrets exist) reads
// package.json alone and comes out unsigned, exactly as before.
//
// What electron-builder then does by itself: installs Microsoft's signing
// PowerShell module, signs every .exe of the app (the desktop exe, the frozen
// daemon and its helper programs under resources/, the uninstaller) and the
// installer, with a timestamp, and writes the publisher name into the app's
// update file so the installed app only takes updates signed by the same name.
//
// The three AZURE_* values are read by Microsoft's module from the environment
// (they are never passed on a command line). The other three are not secret.
//
// See docs/SIGNING.md.

"use strict";

// The certificate's subject name (CN). It is what Windows shows as the
// publisher, and what an installed app checks every later update against, so
// changing it strands installed copies. IJ_SIGN_PUBLISHER overrides it only for
// a test certificate.
const PUBLISHER = "RealDealCPA.Ai LLC";

const REQUIRED = [
  "AZURE_TENANT_ID",
  "AZURE_CLIENT_ID",
  "AZURE_CLIENT_SECRET",
  "IJ_SIGN_ENDPOINT",
  "IJ_SIGN_ACCOUNT",
  "IJ_SIGN_PROFILE",
];

function missing(env) {
  return REQUIRED.filter((name) => !String(env[name] || "").trim());
}

function signedConfig(env, base) {
  const absent = missing(env);
  if (absent.length) {
    // Loud on purpose: a half-configured signing setup must fail the build,
    // never ship an unsigned installer that looks like a signed release.
    throw new Error(
      "signed build asked for, but these values are missing: " + absent.join(", ")
    );
  }
  const endpoint = String(env.IJ_SIGN_ENDPOINT).trim();
  if (!/^https:\/\/[a-z0-9.-]+\.codesigning\.azure\.net\/?$/i.test(endpoint)) {
    throw new Error(
      "IJ_SIGN_ENDPOINT must be the signing account's own address, like https://eus.codesigning.azure.net/"
    );
  }
  return {
    ...base,
    win: {
      ...base.win,
      azureSignOptions: {
        publisherName: String(env.IJ_SIGN_PUBLISHER || "").trim() || PUBLISHER,
        endpoint,
        codeSigningAccountName: String(env.IJ_SIGN_ACCOUNT).trim(),
        certificateProfileName: String(env.IJ_SIGN_PROFILE).trim(),
      },
    },
  };
}

const config = signedConfig(process.env, require("./package.json").build);

module.exports = config;
// For the tests (electron-builder ignores unknown non-enumerable members).
Object.defineProperty(module.exports, "__test", {
  enumerable: false,
  value: { PUBLISHER, REQUIRED, missing, signedConfig },
});
