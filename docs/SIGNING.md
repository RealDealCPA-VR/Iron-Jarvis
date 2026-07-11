# Code-signing the Windows installer

Iron Jarvis ships unsigned today, so every new download trips SmartScreen
("Windows protected your PC" → More info → Run anyway) and browsers flag the
`.exe` as uncommonly downloaded. Signing fixes both by attaching a verified
identity to the binaries and letting Microsoft build reputation for them.
This page is what it actually takes, as of mid-2026.

## TL;DR — what is already wired vs. what YOU must supply

The build is **already plumbed for signing** and stays green whether or not you
configure it:

- `desktop/sign.js` is a custom electron-builder signing hook. It signs each
  artifact **only when signing credentials are in the environment**; with no
  creds it logs `code-signing skipped (no cert configured) — installer will be
  unsigned` and returns, so the build still produces a working (unsigned)
  installer.
- `desktop/build-installer.ps1` and `.github/workflows/release.yml` both run a
  `Get-AuthenticodeSignature` verify pass afterward: if creds were present they
  **fail the build unless the installer is validly signed AND timestamped**; if
  not, they just report "unsigned" and pass.
- `release.yml` passes all signing secrets through as env (absent secrets are
  empty strings, so nothing breaks) and best-effort-installs
  `trusted-signing-cli` on the runner.

**What is EXTERNAL and user-supplied — Iron Jarvis cannot do this for you:**

1. **Obtain a real code-signing certificate.** This costs money and requires
   identity validation; no certificate is (or can be) invented here. Azure
   Trusted Signing (~$9.99/mo) is recommended — see the options table below.
2. **Add the repo secrets** (GitHub → repo → Settings → Secrets and variables →
   Actions → New repository secret). For **Azure Trusted Signing** add all six:

   | Secret | Value |
   |---|---|
   | `AZURE_TENANT_ID` | service-principal tenant id |
   | `AZURE_CLIENT_ID` | service-principal (app) client id |
   | `AZURE_CLIENT_SECRET` | service-principal client secret |
   | `IJ_SIGN_ENDPOINT` | Trusted Signing endpoint, e.g. `https://wus2.codesigning.azure.net/` |
   | `IJ_SIGN_ACCOUNT` | Trusted Signing account name |
   | `IJ_SIGN_PROFILE` | certificate profile name |

   For a **signtool / eSigner** cert instead, add `IJ_SIGNTOOL_PATH` (full path
   to `signtool.exe` on the runner) and `IJ_SIGN_SHA1` (cert thumbprint).
   Optional: `IJ_TIMESTAMP_URL` (defaults to
   `http://timestamp.acs.microsoft.com`).
3. **Set `publisherName`** in `desktop/package.json` → `build.win` to the
   **exact subject CN of your certificate** (it is a placeholder today).
4. **Flip `verifyUpdateCodeSignature` to `true`** in the same block *after* your
   first signed release ships. It is `false` today on purpose: with a placeholder
   `publisherName` and unsigned binaries, electron-updater would otherwise reject
   every self-update. Caveat: the transition update (unsigned → first signed
   release) is not signature-verified; every release after that must stay signed
   with the same publisher or installed apps will refuse to update.

Until you do steps 1–3, **builds are unsigned** and Windows shows a SmartScreen
"unknown publisher" warning on install. That is expected and does not break
anything.

## The ground rules (why this isn't just "buy a .pfx")

- Since **June 2023** (CA/Browser Forum rules), ALL publicly-trusted
  code-signing certificates — OV and EV alike — must keep their private key in
  **FIPS 140-2 Level 2+ hardware**. CAs deliver them on a **USB token**, via
  their **cloud-signing service**, or into an HSM you control. A bare `.pfx`
  file you copy into CI no longer exists.
- Signing identifies a **legal identity**: either a registered business or a
  validated individual. Expect document checks, a verifiable phone listing,
  and (for individuals) notarized or video ID verification.
- **Timestamping** (free, part of the `signtool` invocation) keeps signatures
  valid after the certificate expires — always use it.

## The three realistic options

| | Azure Trusted Signing | OV certificate | EV certificate |
|---|---|---|---|
| Cost | **~$9.99/month** | ~$70–400/year (Certum is the budget end, DigiCert the premium) | ~$250–700/year |
| Who can get it | Org with **3+ years** of verifiable history, or a validated **individual** (US-verifiable identity; needs an Azure subscription) | Business **or individual** | **Registered business only** |
| Key custody | Microsoft-managed HSM, short-lived certs auto-rotated | USB token or the CA's cloud signer (SSL.com eSigner, Certum SimplySign) | Same |
| SmartScreen | Reputation ramps quickly (Microsoft-operated) | Reputation builds over downloads — expect days-to-weeks of residual warnings on a fresh cert | Fastest ramp historically; instant bypass is no longer *guaranteed*, but in practice it's the smoothest |
| CI friendliness | **Best** — pure cloud, GitHub Action + electron-builder support | Token = bad in CI (must sign locally); cloud signer = fine | Same as OV |

**Recommendation for this project:** if the business entity (RealDealCPA) has
3+ years of verifiable registration, **Azure Trusted Signing** is the cheapest
and the only one that drops into the existing GitHub-Actions release flow with
zero hardware. Otherwise a **Certum or SSL.com OV cert with cloud signing**
(individual validation allowed) is the fallback.

## What you'd actually need to do

1. **Pick the identity**: business registration documents (state registry,
   EIN, a phone number listed somewhere verifiable — a DUNS entry helps) or a
   personal ID + notary/video check for individual validation. Validation
   takes ~2–5 business days (org) to ~1–2 weeks (individual).
2. **Enroll**:
   - *Trusted Signing*: Azure subscription → create a Trusted Signing account
     + a "Public Trust" certificate profile → pass identity validation.
   - *OV*: order from the CA, complete validation, choose **cloud signing**
     (not a USB token) if CI signing matters.
3. **Wire it into the build** (the release is built by
   `.github/workflows/release.yml` → `desktop/build-installer.ps1` →
   electron-builder):
   - *Trusted Signing*: electron-builder supports it natively via
     `win.azureSignOptions` (endpoint, account name, profile name) with Azure
     credentials (`AZURE_TENANT_ID`/`CLIENT_ID`/`CLIENT_SECRET`) as GitHub
     secrets. electron-builder then signs every `.exe`/`.dll` and the
     installer automatically.
   - *OV cloud signing*: the CA's CLI (eSigner/SimplySign) hooks in as a
     custom `win.sign` script, credentials as GitHub secrets.
   - *USB token*: CI cannot reach it — you'd build+sign locally with the
     token attached and publish manually. Avoid if possible.
4. **Set `publisherName`** in `desktop/package.json` `build.win` to exactly
   match the certificate's subject — electron-updater verifies the signature
   of downloaded updates against it (`verifyUpdateCodeSignature`), which turns
   the auto-updater into a real chain of trust.
5. **The transition release**: an already-installed unsigned app updates to a
   signed release without issue. From then on, every release must be signed
   with the same publisher or installed apps will refuse the update — so once
   you start signing, keep the cert renewed.

## What signing does NOT do

It doesn't scan or endorse the code; it says "this binary came from this
identity and wasn't tampered with." SmartScreen still applies reputation on
top — a brand-new OV cert sees some residual warnings until enough machines
have installed it cleanly.
