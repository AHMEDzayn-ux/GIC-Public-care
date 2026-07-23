#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-shot deploy of the GIC RAG backend to Google Cloud Run.
#
# Re-run this any time you change the code to ship a new revision. It is
# idempotent: safe to run repeatedly. Everything below is portable — the same
# container also runs with `docker run` on any VM (see DEPLOY.md).
#
# Prereqs (already done once during initial setup):
#   - gcloud CLI installed + `gcloud auth login`
#   - a GCP project with billing linked
#   - Secret Manager secrets: GROQ_API_KEY GOOGLE_API_KEY ADMIN_PASSWORD
#     JWT_SECRET ADMIN_EMAIL  (create/update with: deploy-gcp.sh set-secret NAME)
#   - Artifact Registry repo `gic` in $REGION
#
# Usage:
#   ./deploy-gcp.sh                 # build + deploy
#   ./deploy-gcp.sh set-secret NAME # (re)set one secret, reading value from stdin
# ---------------------------------------------------------------------------
set -euo pipefail

# ---- Config (edit these if you move projects/regions) ---------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-gic-backend}"
REPO="gic"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:$(date +%Y%m%d-%H%M%S)"

# ---- Helper: set a secret from stdin --------------------------------------
if [[ "${1:-}" == "set-secret" ]]; then
  name="${2:?usage: deploy-gcp.sh set-secret SECRET_NAME}"
  echo "Paste value for ${name}, then Ctrl-D:"
  val="$(cat)"
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$val" | gcloud secrets versions add "$name" --data-file=-
  else
    printf '%s' "$val" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic
  fi
  echo "OK: $name updated."
  exit 0
fi

echo ">> Project: $PROJECT_ID   Region: $REGION   Service: $SERVICE"
echo ">> Building image: $IMAGE"
gcloud builds submit --config cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" .

echo ">> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 4Gi \
  --cpu 2 \
  --min-instances 1 \
  --max-instances 3 \
  --timeout 600 \
  --concurrency 40 \
  --startup-probe "tcpSocket.port=8080,periodSeconds=30,failureThreshold=20,timeoutSeconds=5" \
  --set-env-vars "ENVIRONMENT=production,LOG_LEVEL=INFO,USE_RERANKING=true" \
  --set-secrets "GROQ_API_KEY=GROQ_API_KEY:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest,ADMIN_PASSWORD=ADMIN_PASSWORD:latest,JWT_SECRET=JWT_SECRET:latest,ADMIN_EMAIL=ADMIN_EMAIL:latest"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "=========================================================="
echo " Deployed.  Backend URL: $URL"
echo " Set this in Vercel as:  VITE_API_URL=$URL"
echo "=========================================================="
