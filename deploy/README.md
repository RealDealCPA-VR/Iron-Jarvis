# Deploy configs

Provider-specific deployment specs and guides for Iron Jarvis. Start with the centerpiece guide,
[**`../DEPLOY.md`**](../DEPLOY.md) — one-click buttons, per-provider walkthroughs, the required env
vars table, and the security checklist.

All of these build the same two containers from the repo Dockerfiles: the **daemon** (`Dockerfile`,
port `8787`) and the **dashboard** (`Dockerfile.dashboard`, port `3000`).

## Index

| Provider | File | Style |
|----------|------|-------|
| **Local / any VPS** | [`../docker-compose.yml`](../docker-compose.yml) | `docker compose up` — daemon + dashboard |
| **Render** | [`../render.yaml`](../render.yaml) | Blueprint (two web services + persistent disk) |
| **Railway** | [`../railway.toml`](../railway.toml) | Config-as-code (daemon) + UI steps for the dashboard |
| **DigitalOcean** | [`../.do/app.yaml`](../.do/app.yaml) | App Platform spec (two services) |
| **AWS** | [`aws.md`](aws.md) | App Runner (simplest) or ECS Fargate + EFS |
| **Azure** | [`azure.md`](azure.md) | Container Apps (or Web App for Containers) + Azure Files |

## The three things every deployment needs

1. **`IRONJARVIS_TOKEN`** on the daemon — without it, the public API is wide open (it executes
   tools; treat it like remote code execution).
2. **`NEXT_PUBLIC_IJ_API`** on the dashboard — the public HTTPS URL of the daemon, baked in at
   **build time**.
3. A **persistent volume at `/data`** — `.ironjarvis/` (SQLite + encrypted vault) must survive
   redeploys, and the vault key must be protected/backed up.

See [`../DEPLOY.md`](../DEPLOY.md) for the rest.
