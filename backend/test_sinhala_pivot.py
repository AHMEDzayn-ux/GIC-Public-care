"""Quick manual check of the English-pivot Sinhala flow against a real collection."""
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")  # faiss+torch OpenMP workaround
os.environ.setdefault("OMP_NUM_THREADS", "1")
import sentence_transformers  # noqa: F401  (import torch before faiss; see main.py)

import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from services.rag_pipeline import RAGPipeline
from config import get_settings

print("content_language =", get_settings().content_language)

p = RAGPipeline(collection_name="client_nexus", domain="telecom")
p.vector_store.load_collection("client_nexus")
p.rebuild_bm25_index()

queries = [
    "mata plans gana danaganna oney",          # Singlish: "I want to know about plans"
    "ඔයාලගේ plans මොනවද තියෙන්නේ?",              # Sinhala script: "what plans do you have?"
    "What plans do you offer?",                  # English (control)
]
for q in queries:
    print("=" * 60)
    print("USER:", q)
    r = p.query(question=q, top_k=3, return_sources=True)
    if r.get("sources"):
        print("TOP SRC (en):", r["sources"][0]["text"][:70].replace("\n", " "))
    print("BOT:", r["answer"][:400])
