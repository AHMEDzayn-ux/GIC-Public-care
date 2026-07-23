# Deployment Guide

Backend runs as a **Docker container** — the same image runs on Google Cloud Run,
any VM, or another cloud. Frontend is a static Vite build hosted on **Vercel**.

---

## Architecture

```
  Vercel (frontend, HTTPS)  ──VITE_API_URL──►  Cloud Run (backend, HTTPS)
                                                    │
                                        Secret Manager (API keys, JWT, admin)
```

---

## 1. Backend on Google Cloud Run (current setup)

Everything is scripted. To ship a new revision after any code change:

```bash
./deploy-gcp.sh
```

It builds the image (Cloud Build, from `backend/Dockerfile`) and deploys to Cloud
Run with 4 GiB RAM, 1 warm instance, and a 600 s startup window (the container
rebuilds the FAISS index on boot, which takes a few minutes).

The script prints the backend URL at the end — put it in Vercel (step 3).

### Managing secrets

Secrets live in Google Secret Manager, not in the repo. To change one:

```bash
./deploy-gcp.sh set-secret GROQ_API_KEY   # then paste value + Ctrl-D
./deploy-gcp.sh                           # redeploy to pick it up
```

Secrets used: `GROQ_API_KEY`, `GOOGLE_API_KEY`, `ADMIN_PASSWORD`, `JWT_SECRET`,
`ADMIN_EMAIL`.

### First-time setup on a brand-new GCP project

```bash
gcloud auth login
gcloud projects create MY-PROJECT-ID --name="GIC RAG"
gcloud billing projects link MY-PROJECT-ID --billing-account=YOUR-BILLING-ID
gcloud config set project MY-PROJECT-ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud artifacts repositories create gic --repository-format=docker --location=us-central1
# create the 5 secrets:
for s in GROQ_API_KEY GOOGLE_API_KEY ADMIN_PASSWORD JWT_SECRET ADMIN_EMAIL; do
  ./deploy-gcp.sh set-secret $s
done
# grant the Cloud Run runtime SA access to each secret:
PNUM=$(gcloud projects describe MY-PROJECT-ID --format='value(projectNumber)')
for s in GROQ_API_KEY GOOGLE_API_KEY ADMIN_PASSWORD JWT_SECRET ADMIN_EMAIL; do
  gcloud secrets add-iam-policy-binding $s \
    --member="serviceAccount:${PNUM}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
./deploy-gcp.sh
```

---

## 2. Rehosting the backend on ANY VM (portability)

The container is self-contained. On a fresh Ubuntu VM (any cloud, ≥2 GB RAM,
4 GB recommended) with Docker installed:

```bash
git clone <your-repo> && cd Agent-framework

# Build the image (root context, backend Dockerfile):
docker build -t gic-backend -f backend/Dockerfile .

# Run it, passing the same env the Cloud Run secrets provided:
docker run -d --name gic-backend -p 8080:8080 \
  -e ENVIRONMENT=production \
  -e LOG_LEVEL=INFO \
  -e USE_RERANKING=false \
  -e GROQ_API_KEY=... \
  -e GOOGLE_API_KEY=... \
  -e ADMIN_PASSWORD=... \
  -e JWT_SECRET=... \
  -e ADMIN_EMAIL=... \
  gic-backend
```

The app now listens on `:8080`. For HTTPS on a plain VM, put **Caddy** in front
(automatic Let's Encrypt) — Cloud Run does this for you, a raw VM does not:

```
# /etc/caddy/Caddyfile
api.yourdomain.com {
    reverse_proxy localhost:8080
}
```

Then update `VITE_API_URL` in Vercel to the new URL and redeploy the frontend.

> Tip: keep a `.env` file with the values and use `docker run --env-file .env ...`
> so moving hosts is just "copy repo + .env, run two commands."

---

## 3. Frontend on Vercel

1. Import the repo in Vercel, set **Root Directory** = `frontend`.
2. Build command `npm run build`, output dir `dist` (Vite defaults).
3. Add an environment variable:
   ```
   VITE_API_URL = <backend URL from deploy-gcp.sh>
   ```
4. Deploy. To point at a different backend later, change `VITE_API_URL` and redeploy.

---

## Cost notes ($300 free credit)

- Cloud Run bills mainly for the 1 always-warm instance's memory + CPU. With
  `--min-instances 1` the ~5 min boot only happens on deploy, not per request.
- Set `--min-instances 0` in `deploy-gcp.sh` to scale to zero (cheaper, but every
  cold request then waits for the full boot/reindex). Warm is the better default.
