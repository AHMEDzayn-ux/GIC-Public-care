"""
Build/rebuild the "gic" tenant and index all gic.gov.lk content into its
FAISS collection (client_gic).

Run order (from backend/, with venv active):
    python scripts/gic/fetch_gic_data.py          # services/organizations/news + forms manifest
    python scripts/gic/fetch_gic_static_pages.py  # about page + homepage
    python scripts/gic/download_gic_forms.py      # ~96 form PDFs
    python scripts/ingest_gic.py                  # this script - build/rebuild the tenant

Rebuild semantics: each run of this script is a fresh Python process, so
MultiClientRAGPipeline().create_pipeline() always builds a brand-new, empty
in-memory RAGPipeline (RAGPipeline.__init__ never lazy-loads a prior
collection from disk - only get_pipeline()/_load_client_from_disk() does
that). index_documents() then persists the accumulated in-memory state after
every call via VectorStoreService.persist(), which fully overwrites
{name}.index / {name}_metadata.pkl rather than appending to them. So simply
never touching the old on-disk files until the fresh build is complete is
enough to make re-running this script a clean full rebuild - no explicit
delete step is needed.
"""

import os  # faiss/torch OpenMP guard - must precede torch & faiss imports (see main.py)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
import sentence_transformers  # noqa: F401  (must import before faiss; see main.py)

import json
import sys
from collections import Counter
from pathlib import Path

# This script lives in backend/scripts/; add backend/ to the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
from database import SessionLocal
from services import client_store
from services.rag_pipeline import MultiClientRAGPipeline
from logger import get_logger

logger = get_logger(__name__)

GIC_DIR = Path(__file__).resolve().parent.parent.parent / "documents" / "gic"
CLIENT_SLUG = "gic"

GIC_PERSONA = (
    "a helpful assistant for Sri Lanka's Government Information Centre, "
    "answering citizens' questions about government services, organizations, "
    "forms, and procedures using only the official GIC knowledge base"
)


def ensure_client(db) -> None:
    client = client_store.get_client(db, CLIENT_SLUG)
    if client is None:
        logger.info(f"Creating client '{CLIENT_SLUG}'")
        client_store.create_client(
            db,
            slug=CLIENT_SLUG,
            name="Government Information Centre",
            description="Citizen services chatbot for gic.gov.lk",
            domain="generic",
            persona=GIC_PERSONA,
            bot_name="GIC Assistant",
            greeting="Hi! Ask me about government services, forms, or organizations.",
            owner_id=None,
        )
    else:
        logger.info(f"Client '{CLIENT_SLUG}' already exists")


def index_json_source(pipeline, filename: str, content_type: str, text_fields, metadata_fields):
    path = GIC_DIR / filename
    if not path.exists():
        logger.warning(f"Missing {path}, skipping {content_type}")
        return 0
    result = pipeline.index_documents(
        file_paths=[str(path)],
        metadata={"content_type": content_type, "source_type": "web"},
        json_text_fields=text_fields,
        json_metadata_fields=metadata_fields,
    )
    return result.get("total_chunks", 0)


def index_forms(pipeline) -> int:
    manifest_path = GIC_DIR / "forms_manifest.json"
    if not manifest_path.exists():
        logger.warning(f"Missing {manifest_path}, skipping forms")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seen_paths = set()
    total = 0
    for form in manifest:
        local_path = form.get("local_path")
        if not local_path or not Path(local_path).exists():
            logger.warning(f"Missing local PDF for form '{form.get('name')}', skipping")
            continue
        if local_path in seen_paths:
            continue  # several manifest entries can point at the same underlying PDF
        seen_paths.add(local_path)

        # Some government form PDFs are scanned/image-only with no text layer -
        # pdfplumber/pypdf extract nothing, which would otherwise crash the
        # shared embed_batch() call on an empty text list. Skip those instead
        # of letting one bad file abort the whole ingestion run.
        try:
            extracted = pipeline.doc_loader.load_pdf(local_path)
        except Exception as e:
            logger.warning(f"Could not read PDF '{form.get('name')}' ({local_path}): {e}")
            continue
        if not extracted or not extracted.strip():
            logger.warning(f"No extractable text in '{form.get('name')}' ({local_path}) - likely scanned/image-only, skipping")
            continue

        result = pipeline.index_documents(
            file_paths=[local_path],
            metadata={
                "content_type": "forms",
                "source_type": "web",
                "url": form["url"],
                "category": form.get("category", ""),
                "form_name": form.get("name", ""),
            },
        )
        total += result.get("total_chunks", 0)
    return total


def main():
    database.init_db()
    db = SessionLocal()
    try:
        ensure_client(db)
    finally:
        db.close()

    manager = MultiClientRAGPipeline()
    pipeline = manager.create_pipeline(client_id=CLIENT_SLUG, domain="generic", system_role=GIC_PERSONA)

    counts = {}
    counts["services"] = index_json_source(
        pipeline, "services.json", "services",
        text_fields=["title", "summary", "description"],
        metadata_fields=["url", "category", "top_category", "category_type"],
    )
    counts["organizations"] = index_json_source(
        pipeline, "organizations.json", "organizations",
        text_fields=["title", "description"],
        metadata_fields=["url", "category", "phone", "email", "address", "fax", "website"],
    )
    counts["news"] = index_json_source(
        pipeline, "news.json", "news",
        text_fields=["title", "summary", "description"],
        metadata_fields=["url", "category"],
    )
    counts["static_page"] = index_json_source(
        pipeline, "static_pages.json", "static_page",
        text_fields=["title", "description"],
        metadata_fields=["url", "category"],
    )
    counts["forms"] = index_forms(pipeline)

    metadatas = pipeline.vector_store.collections.get(pipeline.collection_name, {}).get("metadatas", [])
    breakdown = Counter(m.get("content_type", "unknown") for m in metadatas)
    total = pipeline.vector_store.get_collection_count(pipeline.collection_name)

    print("\n=== GIC ingestion summary ===")
    for content_type, n in counts.items():
        print(f"  {content_type}: {n} chunks indexed")
    print(f"\nCollection '{pipeline.collection_name}' total: {total} chunks")
    print(f"Breakdown by content_type in stored metadata: {dict(breakdown)}")


if __name__ == "__main__":
    main()
