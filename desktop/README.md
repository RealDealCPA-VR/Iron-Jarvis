# Iron Jarvis — Desktop

A thin **Electron** wrapper that turns Iron Jarvis into a native desktop app. It
launches the local Python daemon and the Next.js dashboard for you, waits for
them to come up behind a splash screen, then opens the dashboard in a real
window — no terminal juggling.

> **Scope:** this is a *dev-mode* wrapper. It runs the daemon and dashboard
> **straight out of this repo** (the folder one level up). It does **not** yet
> bundle Python or a prebuilt dashboard into a standalone installer — that's a
> future step (see [Packaging](#packaging) and [Roadmap](#roadmap)).

```
desktop/
├── package.json     # iron-jarvis-desktop — its own deps, separate from the dashboard
├── main.js          # Electron main process: spawns daemon + dashboard, manages windows
├── preload.js       # exposes window.ironjarvis = { isDesktop, version }
├── loading.html     # the lighthouse "Starting Iron Jarvis…" splash (inline SVG)
└── assets/
    ├── icon.png     # placeholder app icon (replace with your brand icon)
    └── README.md
```

## What it does

On launch, `main.js`:

1. Shows the **"Starting Iron Jarvis…"** splash window (a glowing lighthouse).
2. Verifies `uv` and `pnpm` are on your `PATH` (friendly error dialog if not).
3. Spawns the **daemon**:
   `uv run ironjarvis serve --host 127.0.0.1 --port 8787 --root <repoRoot>`
   (cwd = repo root, one directory above `desktop/`).
4. Spawns the **dashboard**: `pnpm start` (cwd = `../dashboard`, `PORT` set from env).
5. Polls `http://127.0.0.1:3000` for up to ~30s until it answers.
6. Opens the main **1440×900** window on `http://localhost:3000` and closes the splash.
7. On quit, **kills both child processes** (`taskkill /T /F` on Windows) so nothing
   is left listening.

External links (target `_blank` / off-origin navigations) open in your system
browser. The app menu has reload / force-reload / toggle devtools / quit.

## Prerequisites

This wrapper drives the existing repo tooling, so you need:

- **Node 20** and **pnpm 10** (`npm i -g pnpm`)
- **uv** — the Python runner (<https://docs.astral.sh/uv/>)
- The Python deps installable by uv (handled automatically by `uv run`)
- **The dashboard built once** (Next.js `start` serves a *production* build):

  ```bash
  cd ../dashboard
  pnpm install
  pnpm build
  ```

  If you skip this, the splash will time out and show a dialog telling you to run
  `pnpm build`.

## Run

```bash
cd desktop
pnpm install      # installs Electron (downloads the Electron binary — expected)
pnpm start        # boots the daemon + dashboard and opens the native window
```

`pnpm dev` is an alias for `pnpm start`.

### Configuration

| Env var             | Default | Purpose                        |
| ------------------- | ------- | ------------------------------ |
| `IJ_DAEMON_PORT`    | `8787`  | Port for `ironjarvis serve`    |
| `IJ_DASHBOARD_PORT` | `3000`  | Port for the Next.js dashboard |

Example:

```bash
IJ_DAEMON_PORT=9001 IJ_DASHBOARD_PORT=3100 pnpm start
```

## Packaging

Build the SELF-CONTAINED Windows installer. The installed app needs **no Python /
uv / Node / pnpm** — it bundles a PyInstaller-frozen daemon **and** the Next.js
standalone dashboard:

```powershell
cd desktop
pnpm run dist:full   # build-installer.ps1: freeze daemon → build dashboard → NSIS installer in desktop/release/
```

> **Use `dist:full`, not bare `pnpm dist`.** `pnpm dist` runs ONLY electron-builder
> — it does not freeze the daemon or build the dashboard, so it silently produces a
> broken installer (electron-builder only *warns* on the missing `extraResources`).
> `dist:full` (and CI via `.github/workflows/release.yml`) freezes the daemon,
> builds the standalone dashboard, stages both into `extraResources`, and stamps
> the version from the git tag / `pyproject.toml`.

The `electron-builder` config lives in `package.json` under `build`:

- `appId`: `com.realdealcpa.ironjarvis` · `productName`: `Iron Jarvis` · `win.target`: `nsis`
- `extraResources`: the frozen daemon (`packaging/dist/ironjarvis` → `daemon`) and the
  standalone dashboard (`dashboard/.next/standalone` → `dashboard`)
- `publish`: GitHub Releases with `releaseType: release` — the packaged app
  auto-updates via electron-updater

## Roadmap

The app is already a true standalone install (frozen daemon + bundled dashboard;
no repo, no global `uv`/`pnpm` required). Remaining:

- **Code-sign** the installer + daemon to remove the SmartScreen "unknown
  publisher" warning on a fresh machine (needs an OV/EV cert or Azure Trusted
  Signing).

## Troubleshooting

- **"missing prerequisites" dialog** — install `uv` and/or `pnpm`, then relaunch.
- **"dashboard did not start" dialog** — you almost certainly haven't built the
  dashboard: `cd ../dashboard && pnpm install && pnpm build`. Watch the terminal
  for `[daemon]` / `[dashboard]` logs.
- **Port already in use** — set `IJ_DAEMON_PORT` / `IJ_DASHBOARD_PORT`.
