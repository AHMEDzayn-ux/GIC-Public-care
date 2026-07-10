"""Test the agent_chat path (used by voice + WhatsApp) for Sinhala/Singlish/English."""
import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
import sentence_transformers  # noqa: F401

import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from services.rag_pipeline import RAGPipeline

p = RAGPipeline(collection_name="client_nexus", domain="telecom")
p.vector_store.load_collection("client_nexus")
p.rebuild_bm25_index()

for q in [
    "mata plans gana danaganna oney",       # Singlish
    "ඔයාලගේ business plan එකේ මිල කීයද?",     # Sinhala script
    "How much is the business plan?",         # English control
]:
    print("=" * 60)
    print("USER:", q)
    r = p.agent_chat(q, [], top_k=4)
    print("BOT:", (r.get("answer") or "")[:350])
