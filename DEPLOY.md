<div align="center">

# 🚀 Deploy Iron Jarvis

**Two containers, one click (or one `docker compose up`).**

</div>

Iron Jarvis ships as two services:

| Service | Image | Port | What it is |
|---------|-------|------|------------|
| **daemon** | [`Dockerfile`](Dockerfile) | `8787` | FastAPI API — the brain. **It executes tools; treat it like remote code execution.** |
| **dashboard** | [`Dockerfile.dashboard`](Dockerfile.dashboard) | `3000` | Next.js control center — the UI you drive it from. |

The dashboard talks to the daemon **from your browser**, so the daemon must be reachable at a
public URL that the browser can hit (`NEXT_PUBLIC_IJ_API`).

> ⚠️ **Before you expose this to the internet, read the [Security checklist](#-security-checklist).**
> An unprotected daemon lets anyone run agents — and tools — on your box.

---

## TL;DR

**Local / your own VPS** (uses [`docker-compose.yml`](docker-compose.yml)):
```bash
IRONJARVIS_TOKEN=$(openssl rand -hex 32) \
PUBLIC_API_URL=https://api.your-host.tld \
docker compose up -d --build
# daemon → :8787   dashboard → :3000   state → ./data (mounted at /data)
```

**One-click to a managed host:**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/RealDealCPA-VR/Iron-Jarvis)
[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new)
[![Deploy to DigitalOcean](https://www.deploytodo.com/do-btn-blue.svg)](https://cloud.digitalocean.com/apps/new?repo=https://github.com/RealDealCPA-VR/Iron-Jarvis/tree/master)

After any deploy: **set `IRONJARVIS_TOKEN`**, point the dashboard's `NEXT_PUBLIC_IJ_API` at the
daemon's public URL, and make sure `.ironjarvis/` sits on a persistent `/data` volume.

---

## Pick a provider

| Provider | Spec file | Notes |
|----------|-----------|-------|
| **Render** | [`render.yaml`](render.yaml) | Blueprint: two web services + a 1GB persistent disk. Cleanest one-click. |
| **Railway** | [`railway.toml`](railway.toml) | One service per Dockerfile → deploy daemon + dashboard as two services. |
| **DigitalOcean** | [`.do/app.yaml`](.do/app.yaml) | App Platform; **ephemeral FS** — use a Droplet/managed DB for stateful use. |
| **AWS** | [`deploy/aws.md`](deploy/aws.md) | App Runner (simplest) or ECS Fargate + EFS for persistence. |
| **Azure** | [`deploy/azure.md`](deploy/azure.md) | Container Apps (or Web App for Containers) + Azure Files. |

---

### Render

1. Click **Deploy to Render** above (or **New → Blueprint** and point it at the repo). Render reads
   [`render.yaml`](render.yaml) and creates `iron-jarvis-daemon` (with a `/data` disk) and
   `iron-jarvis-dashboard`.
2. `IRONJARVIS_TOKEN` is auto-generated for the daemon. Note the daemon's URL once it's live
   (e.g. `https://iron-jarvis-daemon.onrender.com`).
3. Open **iron-jarvis-dashboard → Environment**, set `NEXT_PUBLIC_IJ_API` to that URL (and, if you
   use a token, `NEXT_PUBLIC_IJ_TOKEN` to the **same** value as the daemon's token), then redeploy
   the dashboard — `NEXT_PUBLIC_*` is baked in at build time.
4. *(Optional)* add `ANTHROPIC_API_KEY` on the daemon for live Claude models.

### Railway

1. **New Project → Deploy from GitHub repo** → select the repo. Railway uses
   [`railway.toml`](railway.toml) to build the **daemon** (`Dockerfile`).
2. On the daemon service set `IRONJARVIS_TOKEN`, `IRONJARVIS_ROOT=/data` (and optionally
   `ANTHROPIC_API_KEY`), and attach a **Volume mounted at `/data`**.
3. Add a **second service** from the same repo → Settings → Dockerfile Path =
   `Dockerfile.dashboard`, Start Command = `pnpm start`. Set
   `NEXT_PUBLIC_IJ_API = https://${{daemon.RAILWAY_PUBLIC_DOMAIN}}` (Railway reference variable),
   and optionally `NEXT_PUBLIC_IJ_TOKEN`.
4. Generate a public domain for each service. Healthcheck is `/health`.

### DigitalOcean App Platform

1. Click **Deploy to DigitalOcean** above (or **Apps → Create → GitHub**). It reads
   [`.do/app.yaml`](.do/app.yaml) → `daemon` (8787) + `dashboard` (3000), with the dashboard wired
   to `${daemon.PUBLIC_URL}`.
2. Set `IRONJARVIS_TOKEN` (encrypted secret) on the daemon; optionally `ANTHROPIC_API_KEY`.
3. **Persistence:** App Platform is **ephemeral** — `.ironjarvis/` is wiped on redeploy. For
   stateful use, run the daemon on a **Droplet** with `docker compose` + an attached Volume, or
   move to a managed Postgres. The spec is fine for a stateless demo.

### AWS

See [`deploy/aws.md`](deploy/aws.md) — **App Runner** from each Dockerfile (fastest) or **ECS
Fargate** from `docker-compose.yml`, with **EFS** mounted at `/data` for persistence.

### Azure

See [`deploy/azure.md`](deploy/azure.md) — **Azure Container Apps** from the two images, with
**Azure Files** mounted at `/data` for persistence (or **Web App for Containers**).

---

## Required env vars

| Variable | Service | Required? | What it does |
|----------|---------|-----------|--------------|
| **`IRONJARVIS_TOKEN`** | daemon | **Strongly recommended** — *set this!* | Bearer token that protects the public API. **Without it, anyone who finds the URL can drive your agents and execute tools (remote code execution).** Use a long random value (`openssl rand -hex 32`). The daemon enforces auth only when this is set. |
| **`NEXT_PUBLIC_IJ_API`** | dashboard | **Yes** (for any hosted deploy) | The **public** daemon URL the browser calls, e.g. `https://iron-jarvis-daemon.onrender.com`. **Inlined at build time** (Next.js bakes `NEXT_PUBLIC_*`), so it's a Docker **build arg** — set it before/at build and redeploy the dashboard after changing it. Defaults to `http://127.0.0.1:8787` for local dev. |
| **`NEXT_PUBLIC_IJ_TOKEN`** | dashboard | Optional | If you set `IRONJARVIS_TOKEN`, set this to the **same** value so the dashboard authenticates to the daemon. Also build-time inlined. |
| `IRONJARVIS_ROOT` | daemon | Recommended | Point at the mounted volume (`/data`) so `.ironjarvis/` (SQLite + encrypted vault) persists across redeploys. The container serves `--root /data`. |
| `ANTHROPIC_API_KEY` | daemon | Optional | Enables live Claude models. Without it, Iron Jarvis runs the deterministic **mock model** (fully offline). You can also add keys at runtime from the dashboard's Connections page. |

> `PUBLIC_API_URL` and `./data:/data` in [`docker-compose.yml`](docker-compose.yml) are the
> compose-local way to set `NEXT_PUBLIC_IJ_API` (build arg) and the persistent volume.

---

## 🔒 Security checklist

Iron Jarvis is built to run **local-first** under your control. The moment you put it on a public
host, harden it — the daemon **executes tools on your behalf**, so an open daemon is effectively
remote code execution.

- [ ] **Set a token.** Always set `IRONJARVIS_TOKEN` (and the matching `NEXT_PUBLIC_IJ_TOKEN` on the
      dashboard). An unset/empty token means the API is **wide open** to anyone who finds the URL.
- [ ] **Put it behind HTTPS.** Never serve the daemon or dashboard over plain HTTP on the public
      internet — the bearer token and all traffic would be in the clear. Render/Railway/DO/App
      Runner/Container Apps all terminate TLS for you; on a raw VPS put it behind Caddy/nginx/Traefik.
- [ ] **Tighten CORS for production.** The daemon currently allows all origins
      (`allow_origins=["*"]`). Lock it down to your dashboard's exact origin before exposing it.
- [ ] **Keep computer use / browser automation DISABLED** unless you run the daemon inside an
      **isolated, disposable VM**. These tools (and the sandbox) act on the host — only enable them
      where a compromise can't hurt you.
- [ ] **Treat the daemon as remote code execution.** It runs agents that call tools. Restrict who
      can reach it (token + network rules), keep `shell` and other dangerous tools fail-closed
      (the default), and never auto-approve risky actions on a public box.
- [ ] **Protect and back up the volume.** `.ironjarvis/` holds the SQLite DB **and the Fernet
      secrets-vault key**. Keep it on a private, mounted volume (`/data`), back it up, and never
      commit it or expose it publicly — anyone with the volume can decrypt your stored secrets.

---

## Persistence

All durable state lives under **`.ironjarvis/`** (SQLite database + the **Fernet-encrypted secrets
vault** + workspaces/artifacts). The daemon serves with `--root /data`, so this directory must sit
on a **mounted volume at `/data`** — otherwise **it is wiped on every redeploy/restart** and you
lose your sessions, schedules, memory, and vault key.

| Provider | How to persist `/data` |
|----------|------------------------|
| docker-compose | `./data:/data` bind mount (already in [`docker-compose.yml`](docker-compose.yml)) |
| Render | Persistent **Disk** mounted at `/data` (in [`render.yaml`](render.yaml)) |
| Railway | Attach a **Volume** to the daemon at `/data` |
| DigitalOcean App Platform | **Ephemeral** — use a Droplet + Volume, or a managed Postgres |
| AWS | **EFS** (ECS Fargate) or an EBS volume on EC2; App Runner FS is ephemeral |
| Azure | **Azure Files** share mounted at `/data` |

Because SQLite is a single-writer database, run the daemon as a **single instance** (don't
horizontally scale it).
