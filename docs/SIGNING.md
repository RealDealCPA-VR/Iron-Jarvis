# Code-signing the Windows installer

Iron Jarvis is signed with **Microsoft Artifact Signing** (the service that
used to be called Azure Trusted Signing), as **RealDealCPA.Ai LLC**. Windows
shows that name as the publisher when someone runs the installer.

Signing switches on by itself when six values are saved as GitHub secrets.
Until then, releases are built unsigned, exactly as before.

## What is set up in Azure

| Thing | Value |
|---|---|
| Signing account | `ironjarvissign` |
| Identity validation | RealDealCPA.Ai LLC (Public), valid until 2029-02-07 |
| Certificate profile | `ironjarvis-public` (Public Trust) |
| Name on the certificate | `CN=RealDealCPA.Ai LLC, O=RealDealCPA.Ai LLC, L=Melbourne, S=Florida, C=US` |
| Sign-in the build uses | app registration `ironjarvis-github-signing`, with the role **Artifact Signing Certificate Profile Signer** on the account |

## The six GitHub secrets

Repo → Settings → Secrets and variables → Actions.

| Secret | Value |
|---|---|
| `AZURE_TENANT_ID` | the app registration's Directory (tenant) ID |
| `AZURE_CLIENT_ID` | the app registration's Application (client) ID |
| `AZURE_CLIENT_SECRET` | the app registration's client secret **Value** |
| `IJ_SIGN_ENDPOINT` | the signing account's **Account URI** (its Overview page), like `https://eus.codesigning.azure.net/` |
| `IJ_SIGN_ACCOUNT` | `ironjarvissign` |
| `IJ_SIGN_PROFILE` | `ironjarvis-public` |

Only the client secret is a password. Never put it in a file, a chat or a
commit; type it straight into GitHub.

## How the build signs

- `desktop/build-installer.ps1` looks for the six values. All present: it
  builds with `desktop/builder-signed.js`. None present: an unsigned build.
  Only some present: the build stops and names what is missing.
- `desktop/builder-signed.js` is `package.json`'s own build settings plus the
  signing settings. electron-builder then signs every program in the app (the
  desktop program, the daemon and its helpers, the uninstaller) and the
  installer, each with a timestamp.
- The release workflow's step **Check the installer's signature** asks Windows
  itself: the installer, the app and the daemon must be validly signed by
  RealDealCPA.Ai LLC with a timestamp, and the signed daemon must still start.
  Any miss fails the release before anything is published.
- The release page says "Signed by RealDealCPA.Ai LLC" for a signed build and
  keeps the "isn't code-signed yet" words for an unsigned one.

To try it without publishing anything:

```bash
gh workflow run release.yml --ref <branch> -f dry_run=true
```

## After the first signed release: two things to do

1. **Lock it.** Run this once, so a build that comes out unsigned fails
   instead of being published:

   ```bash
   gh variable set IJ_REQUIRE_SIGNED --body true
   ```

   This matters because a signed copy of the app only accepts updates signed
   by the same publisher. An unsigned release would be refused by every
   installed copy.
2. **Update the words** in `README.md` and the Help page's "Windows
   SmartScreen on install" entry, which still say the app isn't signed.

## What to keep alive

- **The Azure subscription.** If it lapses, signing fails and no release can
  be published until it is paid.
- **The client secret.** It expires (2 years at most). Before it does, make a
  new one on the app registration and replace `AZURE_CLIENT_SECRET`.
- **The identity validation.** It expires on 2029-02-07; renew it in the
  signing account before then.
- **The publisher name.** Installed copies check each update against
  `RealDealCPA.Ai LLC`. If the LLC's name ever changes, plan a release that
  moves installed copies over before the old certificate name goes away.

## What signing does and does not do

- It proves the installer came from RealDealCPA.Ai LLC and was not changed
  after it was built.
- It does not make the Windows warning vanish on day one. SmartScreen still
  judges a new file by how many people have installed it. With a signed
  installer that trust builds up under the publisher's name and carries over
  to later releases; the first downloads may still see "Windows protected your
  PC", now with the publisher named.
- An unsigned copy that is already installed updates to the first signed
  release normally.

## If Microsoft's service is ever not an option

The fallback is an organisation (OV) certificate for the LLC from a
certificate authority that offers cloud signing (SSL.com, Certum, Sectigo).
A certificate on a USB token cannot be used by the GitHub build.
