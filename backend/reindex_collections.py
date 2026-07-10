"""
Re-index all FAISS collections with the CURRENT embedding model.

Why: switching embedding models (e.g. English-only all-MiniLM-L6-v2 ->
multilingual paraphrase-multilingual-MiniLM-L12-v2) makes previously stored
vectors incompatible, even when the dimension is unchanged (384). The stored
document TEXT is kept in each *_metadata.pkl, so we can rebuild every index
in place without re-uploading source files.

Usage (from backend/, with venv active):
    python reindex_collections.py            # re-index all collections
    python reindex_collections.py client_nexus university_docs   # only these

Old .index files are backed up to vector_stores/faiss/_backup_<timestamp>/
before being overwritten, so you can revert if needed.
"""

import os  # faiss/torch OpenMP guard — must precede torch & faiss imports (see main.py)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
import sentence_transformers  # noqa: F401  (must import before faiss; see main.py)

import sys
import pickle
import shutil
from datetime import datetime
from pathlib import Path

import faiss

from config import get_settings
from services.embeddings import EmbeddingsService
from logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


def find_collections(faiss_dir: Path):
    """Return collection names that have a persisted metadata file."""
    return sorted(
        p.name[: -len("_metadata.pkl")]
        for p in faiss_dir.glob("*_metadata.pkl")
    )


def reindex(collection: str, faiss_dir: Path, embedder: EmbeddingsService, backup_dir: Path):
    index_path = faiss_dir / f"{collection}.index"
    meta_path = faiss_dir / f"{collection}_metadata.pkl"

    if not meta_path.exists():
        logger.error(f"[{collection}] metadata file missing, skipping: {meta_path}")
        return False

    with open(meta_path, "rb") as f:
        meta = pickle.load(f)

    documents = meta.get("documents", [])
    if not documents:
        logger.warning(f"[{collection}] no documents stored, skipping")
        return False

    dim = settings.embedding_dimension
    logger.info(f"[{collection}] re-embedding {len(documents)} chunks with {settings.embedding_model}")

    embeddings = embedder.embed_batch(documents, show_progress=True)

    # Backup the old index before overwriting.
    if index_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(index_path, backup_dir / index_path.name)

    # Build a fresh L2 index (must match VectorStoreService.create_collection).
    import numpy as np
    index = faiss.IndexFlatL2(dim)
    index.add(np.array(embeddings, dtype=np.float32))
    faiss.write_index(index, str(index_path))

    # Keep the dimension field in metadata in sync with the new model.
    meta["dimension"] = dim
    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)

    logger.info(f"[{collection}] done: {index.ntotal} vectors written ({dim}-dim)")
    return True


def main(argv):
    faiss_dir = Path(settings.vector_stores_dir) / "faiss"
    if not faiss_dir.exists():
        logger.error(f"No FAISS directory at {faiss_dir}")
        return 1

    requested = argv[1:]
    all_collections = find_collections(faiss_dir)
    targets = requested or all_collections

    if not targets:
        logger.warning("No collections found to re-index.")
        return 0

    unknown = [c for c in targets if c not in all_collections]
    if unknown:
        logger.error(f"Unknown collection(s): {unknown}. Available: {all_collections}")
        return 1

    backup_dir = faiss_dir / f"_backup_{datetime.now():%Y%m%d_%H%M%S}"
    logger.info(f"Re-indexing {len(targets)} collection(s): {targets}")
    logger.info(f"Backups -> {backup_dir}")

    embedder = EmbeddingsService()  # uses settings.embedding_model

    ok = 0
    for c in targets:
        if reindex(c, faiss_dir, embedder, backup_dir):
            ok += 1

    logger.info(f"Re-index complete: {ok}/{len(targets)} succeeded.")
    return 0 if ok == len(targets) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
