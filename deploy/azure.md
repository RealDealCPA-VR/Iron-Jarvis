# Deploy Iron Jarvis to Azure

Recommended: **Azure Container Apps** (serverless containers). The two images come straight from
the repo's Dockerfiles — daemon (`Dockerfile`, port `8787`) and dashboard (`Dockerfile.dashboard`,
port `3000`). **Web App for Containers** works too and is noted at the bottom.

> Heads-up: the dashboard inlines `NEXT_PUBLIC_IJ_API` at **build time** (Next.js bakes
> `NEXT_PUBLIC_*`). Pass it as a Docker **build arg** when building the dashboard image, or rebuild
> once the daemon URL is known.

---

## Azure Container Apps

### 1. Provision and push images to ACR
```bash
RG=iron-jarvis-rg
LOC=eastus
ACR=ironjarvisacr           # must be globally unique

az group create -n $RG -l $LOC
az acr create -n $ACR -g $RG --sku Basic --admin-enabled true
az acr login -n $ACR

# Daemon
az acr build -r $ACR -t iron-jarvis-daemon:latest -f Dockerfile .

# Dashboard — set the PUBLIC daemon URL at build time (fill in after step 2 if needed)
az acr build -r $ACR -t iron-jarvis-dashboard:latest -f Dockerfile.dashboard \
  --build-arg NEXT_PUBLIC_IJ_API=https://REPLACE-WITH-DAEMON-URL .
```

### 2. Create the Container Apps environment + daemon app
```bash
az containerapp env create -n iron-jarvis-env -g $RG -l $LOC

az containerapp create \
  -n iron-jarvis-daemon -g $RG --environment iron-jarvis-env \
  --image $ACR.azurecr.io/iron-jarvis-daemon:latest \
  --registry-server $ACR.azurecr.io \
  --target-port 8787 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --command "ironjarvis" "serve" "--host" "0.0.0.0" "--port" "8787" "--root" "/data" \
  --secrets ij-token=$(openssl rand -hex 32) \
  --env-vars IRONJARVIS_TOKEN=secretref:ij-token IRONJARVIS_ROOT=/data
# Add ANTHROPIC_API_KEY similarly (as a secret) to enable live Claude models.
```
Grab the daemon FQDN:
```bash
az containerapp show -n iron-jarvis-daemon -g $RG --query properties.configuration.ingress.fqdn -o tsv
# e.g. iron-jarvis-daemon.<hash>.eastus.azurecontainerapps.io  (served over HTTPS)
```

### 3. Create the dashboard app
- If you used a placeholder build-arg in step 1, **rebuild** the dashboard image with
  `--build-arg NEXT_PUBLIC_IJ_API=https://<daemon FQDN>` and push.
```bash
az containerapp create \
  -n iron-jarvis-dashboard -g $RG --environment iron-jarvis-env \
  --image $ACR.azurecr.io/iron-jarvis-dashboard:latest \
  --registry-server $ACR.azurecr.io \
  --target-port 3000 --ingress external \
  --env-vars NEXT_PUBLIC_IJ_API=https://<daemon FQDN>
# Optional: NEXT_PUBLIC_IJ_TOKEN=<same value as IRONJARVIS_TOKEN>
```

### 4. Persistence (important)
Container Apps replicas are **ephemeral** — `.ironjarvis/` (SQLite + encrypted vault) is lost on
restart/redeploy unless you attach durable storage. Mount an **Azure Files** share at `/data`:
```bash
# Create a storage account + file share, then link it to the environment
az storage account create -n ironjarvisstg -g $RG -l $LOC --sku Standard_LRS
az storage share-rm create --storage-account ironjarvisstg -n ij-data --quota 1
az containerapp env storage set -g $RG -n iron-jarvis-env \
  --storage-name ij-data --azure-file-account-name ironjarvisstg \
  --azure-file-share-name ij-data --azure-file-account-key <key> --access-mode ReadWrite
```
Then add a volume + volumeMount (`/data`) to the daemon app via `az containerapp update --yaml`
(volumes are only expressible in the YAML form). Back this share up — it holds the vault key.

---

## Alternative: Web App for Containers
Create two **Web App for Containers** (Linux) instances from the ACR images. Set
`WEBSITES_PORT=8787` (daemon) / `3000` (dashboard) and the same env vars. For persistence, enable a
mounted **Azure Files** path at `/data` (Configuration → Path mappings). HTTPS is automatic on the
`*.azurewebsites.net` hostname.

---

## Required environment variables

| Service   | Variable               | Notes |
|-----------|------------------------|-------|
| daemon    | `IRONJARVIS_TOKEN`     | **Set it.** Bearer token guarding the public API (store as a Container Apps secret). |
| daemon    | `IRONJARVIS_ROOT`      | `/data` — the Azure Files mount so state persists. |
| daemon    | `ANTHROPIC_API_KEY`    | Optional; live Claude models. |
| dashboard | `NEXT_PUBLIC_IJ_API`   | Public HTTPS URL of the daemon. **Build-time** (Docker build arg). |
| dashboard | `NEXT_PUBLIC_IJ_TOKEN` | Optional; same value as `IRONJARVIS_TOKEN`. |

See [`../DEPLOY.md`](../DEPLOY.md) for the full security checklist.
