# Code-signing the Windows installer

Iron Jarvis ships unsigned today, so every new download trips SmartScreen
("Windows protected your PC" → More info → Run anyway) and browsers flag the
`.exe` as uncommonly downloaded. Signing fixes both by attaching a verified
identity to the binaries and letting Microsoft build reputation for them.
This page is what it actually takes, as of mid-2026.

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
