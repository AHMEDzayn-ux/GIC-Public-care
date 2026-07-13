FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Build context is the repo root (see .do/app.yaml) so documents/gic/ - which
# scripts/ingest_gic.py needs to rebuild the tenant on every boot - ships in the
# image alongside backend/, preserving the same relative layout config.py and the
# ingest script already assume for local/monorepo runs.
COPY backend backend
COPY documents documents

WORKDIR /app/backend

EXPOSE 8080

# App Platform instances have ephemeral disks (wiped on every redeploy/restart), so
# the sqlite row + FAISS index for the "gic" tenant must be rebuilt on every boot from
# the documents/gic/ sources checked into the image, not run once by hand.
#
# Shell form (not exec/JSON form) so $PORT actually expands - Cloud Run injects its
# own PORT (default 8080); HF Spaces/other hosts that don't set it fall back to 8080.
CMD python scripts/ingest_gic.py && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
