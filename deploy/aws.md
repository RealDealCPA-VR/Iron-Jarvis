# Deploy Iron Jarvis to AWS

Two supported shapes. Pick one:

- **A. App Runner** (simplest) — managed containers straight from each Dockerfile.
- **B. ECS Fargate** — run the existing `docker-compose.yml` as a task.

Both run the two images from the repo: the **daemon** (`Dockerfile`, port `8787`) and the
**dashboard** (`Dockerfile.dashboard`, port `3000`).

> Heads-up: the dashboard bakes `NEXT_PUBLIC_IJ_API` into its bundle at **build time**
> (Next.js inlines `NEXT_PUBLIC_*`). Pass it as a Docker **build arg** when you build the
> dashboard image, or rebuild after the daemon URL is known.

---

## A. AWS App Runner (recommended)

### 1. Build & push the images to ECR
```bash
AWS_ACCOUNT=123456789012
REGION=us-east-1
ECR=$AWS_ACCOUNT.dkr.ecr.$REGION.amazonaws.com
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR
aws ecr create-repository --repository-name iron-jarvis-daemon    || true
aws ecr create-repository --repository-name iron-jarvis-dashboard || true

# Daemon
docker build -t $ECR/iron-jarvis-daemon:latest -f Dockerfile .
docker push  $ECR/iron-jarvis-daemon:latest

# Dashboard — set the PUBLIC daemon URL at BUILD time (fill in after step 2 if needed)
docker build -t $ECR/iron-jarvis-dashboard:latest -f Dockerfile.dashboard \
  --build-arg NEXT_PUBLIC_IJ_API=https://REPLACE-WITH-DAEMON-URL .
docker push  $ECR/iron-jarvis-dashboard:latest
```

### 2. Create the daemon App Runner service
- Source: **Container registry → Amazon ECR**, image `iron-jarvis-daemon:latest`.
- Port: **8787**. Health check path: **`/health`**.
- Start command (override): `ironjarvis serve --host 0.0.0.0 --port 8787 --root /data`
- Environment variables:
  - `IRONJARVIS_TOKEN` = a long random secret (protects the public API)
  - `IRONJARVIS_ROOT` = `/data`
  - `ANTHROPIC_API_KEY` = *(optional; enables live Claude models)*
- Note the assigned URL, e.g. `https://abc123.us-east-1.awsapprunner.com`.

### 3. Create the dashboard service
- Image `iron-jarvis-dashboard:latest`, port **3000**.
- If you used a placeholder build-arg in step 1, **rebuild & push** the dashboard image with
  `--build-arg NEXT_PUBLIC_IJ_API=<daemon URL from step 2>`, then deploy.
- Runtime env (optional): `NEXT_PUBLIC_IJ_TOKEN` = same value as the daemon's `IRONJARVIS_TOKEN`.

### 4. Persistence (important)
App Runner has an **ephemeral filesystem** — `.ironjarvis/` (SQLite + encrypted vault) is lost on
redeploy. For durable state, use **ECS Fargate + EFS** (Option B) or run the daemon on an EC2 box
with `docker compose` and an EBS volume.

---

## B. ECS Fargate (from docker-compose.yml)

1. Push both images to ECR (as in A.1).
2. Convert the compose file to an ECS task with the Docker Compose ECS integration:
   ```bash
   docker context create ecs ironjarvis-ecs
   docker --context ironjarvis-ecs compose up
   ```
   (or translate `docker-compose.yml` into an ECS task definition by hand.)
3. **Persistence:** create an **EFS** file system and mount it into the daemon task at `/data`
   (the daemon serves `--root /data`). EFS survives task restarts, so the SQLite DB and the
   encrypted vault key persist. Back this volume up.
4. Put both services behind an **Application Load Balancer** with **HTTPS** (ACM cert):
   - daemon target group → port `8787`, health check `/health`
   - dashboard target group → port `3000`
5. Set the env vars exactly as in Option A (`IRONJARVIS_TOKEN`, `IRONJARVIS_ROOT=/data`,
   `NEXT_PUBLIC_IJ_API` = the daemon's public HTTPS URL, optional `ANTHROPIC_API_KEY` /
   `NEXT_PUBLIC_IJ_TOKEN`).

---

## Required environment variables

| Service   | Variable               | Notes |
|-----------|------------------------|-------|
| daemon    | `IRONJARVIS_TOKEN`     | **Set it.** Bearer token guarding the public API. |
| daemon    | `IRONJARVIS_ROOT`      | `/data` — the mounted volume (EFS/EBS) so state persists. |
| daemon    | `ANTHROPIC_API_KEY`    | Optional; live Claude models. |
| dashboard | `NEXT_PUBLIC_IJ_API`   | Public HTTPS URL of the daemon. **Build-time** (Docker build arg). |
| dashboard | `NEXT_PUBLIC_IJ_TOKEN` | Optional; same value as `IRONJARVIS_TOKEN`. |

See [`../DEPLOY.md`](../DEPLOY.md) for the security checklist (token, HTTPS, CORS, computer-use,
volume backups).
