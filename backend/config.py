"""
Configuration Management for RAG System

Centralizes all configuration settings with environment variable support.
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    
    Uses .env file for local development, environment variables for production.
    """
    
    # API Keys
    groq_api_key: str = ""
    google_api_key: str = ""  # Gemini API (from Google AI Studio)
    openai_api_key: str = ""  # For voice TTS (text-to-speech)
    
    # Database
    database_url: str = "sqlite:///./rag_system.db"
    
    # Admin / auth
    admin_password: str = "changeme"
    admin_email: str = "admin@local"      # bootstrap operator (seeded on first run)
    jwt_secret: str = ""                    # set JWT_SECRET in prod; dev falls back below
    jwt_expire_days: int = 7
    allow_registration: bool = True         # open sign-up (fine pre-launch; lock for prod SaaS)
    
    # Application
    environment: str = "development"
    log_level: str = "INFO"
    
    # Paths
    documents_dir: Path = Path(__file__).parent.parent / "documents"
    vector_stores_dir: Path = Path(__file__).parent.parent / "vector_stores"
    
    # Document Processing
    default_chunk_size: int = 1200
    default_chunk_overlap: int = 200
    
    # Vector Store
    vector_store_type: str = "faiss"  # FAISS is the wired implementation
    chromadb_persist_directory: Optional[str] = None
    
    # Embeddings
    # Multilingual model: handles English AND Sinhala (සිංහල) in one shared index.
    # Same 384 dims as the old English-only all-MiniLM-L6-v2, so the FAISS store
    # config is unchanged — but ALL documents must be RE-INDEXED after switching,
    # because vectors from the old model are incompatible with this one.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = 384  # Dimension for paraphrase-multilingual-MiniLM-L12-v2

    # LLM provider: "gemini" (Google AI Studio) or "groq"
    # Gemini is markedly more fluent in Sinhala than Groq's Llama models, so it's
    # the default for the Sinhala/Singlish setup. Switch back to "groq" if you hit
    # Gemini free-tier daily caps.
    llm_provider: str = "gemini"

    # Language of the knowledge-base DOCUMENTS. Used to transliterate romanized
    # Sinhala ("Singlish") queries into the script that matches the documents,
    # because the multilingual embedding model matches same-language pairs far
    # better than cross-language ones. Options:
    #   "english" -> translate ALL Sinhala/Singlish queries to English for retrieval
    #                (the "English-pivot": keep your English docs, translate only the
    #                 query in and let the answer come back in Sinhala). DEFAULT.
    #   "sinhala" -> your documents are in Sinhala script; convert Singlish queries
    #                to Sinhala script for retrieval.
    #   "auto"    -> leave queries as-is (only sensible if content is truly mixed).
    content_language: str = "english"

    # Groq models
    llm_model: str = "llama-3.3-70b-versatile"  # main answer-generation model
    # Lightweight model for cheap auxiliary calls (query normalization, etc.)
    # so we don't spend premium-model tokens on internal preprocessing.
    llm_utility_model: str = "llama-3.1-8b-instant"

    # Gemini models (used when llm_provider="gemini")
    # NOTE: free-tier daily request caps are low (2.5-flash ≈ 20/day on this
    # project); flash-lite has more headroom. For production, enable Gemini
    # billing and set gemini_model="gemini-2.5-flash" (cheap + best quality).
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_utility_model: str = "gemini-flash-lite-latest"

    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048  # Increased from 512 to allow complete responses

    # Voice (real-time voice-call mode)
    # STT = Groq Whisper (reuses groq_api_key). TTS is swappable and free-first:
    #   "edge"    -> Microsoft neural voices via edge-tts (FREE, no key, natural)  [default]
    #   "gemini"  -> Gemini TTS via google_api_key (natural, but low free-tier quota -> 429s)
    #   "openai"  -> OpenAI tts-1 via openai_api_key (only if a key is set)
    #   "browser" -> no server TTS; the client speaks the text itself (zero cost)
    # On any provider failure/quota/missing-key the server degrades to "browser"
    # automatically so a live demo never breaks.
    tts_provider: str = "edge"
    edge_voice: str = "en-US-AvaNeural"  # MS's most natural/expressive US female voice
    # edge-tts Sinhala voice, auto-selected when the answer is in Sinhala script.
    # Options: "si-LK-ThiliniNeural" (female), "si-LK-SameeraNeural" (male).
    edge_voice_sinhala: str = "si-LK-ThiliniNeural"
    # edge-tts Tamil voice, auto-selected when the answer is in Tamil script.
    # Sri Lankan Tamil: "ta-LK-SaranyaNeural" (female), "ta-LK-KumarNeural" (male).
    edge_voice_tamil: str = "ta-LK-SaranyaNeural"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    tts_voice: str = "Kore"  # Gemini prebuilt voice (used only when tts_provider="gemini")
    # Hybrid Sinhala TTS: the free edge Sinhala voice (Thilini) is flat, so Sinhala
    # answers are spoken with the more expressive Gemini TTS instead — with an
    # automatic fall-back to the edge Sinhala voice if Gemini's (low) free quota is
    # hit, so it's never worse than before. English is unaffected. Set
    # tts_sinhala_provider="edge" to disable and always use the edge Sinhala voice.
    tts_sinhala_provider: str = "gemini"   # "gemini" (expressive) | "edge" (reliable)
    tts_voice_sinhala: str = "Aoede"        # Gemini prebuilt voice for Sinhala output
    # Same hybrid strategy for Tamil: expressive Gemini TTS with an automatic
    # fall-back to the free edge Tamil voice (Saranya) when Gemini's quota is hit.
    tts_tamil_provider: str = "gemini"     # "gemini" (expressive) | "edge" (reliable)
    tts_voice_tamil: str = "Aoede"          # Gemini prebuilt voice for Tamil output
    stt_model: str = "whisper-large-v3"  # Groq Whisper (more accurate than turbo)
    # Whisper transcription language for voice calls:
    #   None -> auto-detect (handles both Sinhala and English callers)  [default]
    #   "si" -> force Sinhala,  "en" -> force English.
    stt_language: Optional[str] = None
    # Speech-to-text engine. Groq Whisper is fast but transcribes Sinhala poorly;
    # Gemini's multimodal audio understanding is far more accurate for Sinhala AND
    # Singlish code-switching, and (verified) accepts the browser's audio/webm;opus
    # directly. Options:
    #   "gemini" -> Gemini audio (best Sinhala; falls back to Whisper on error)  [default]
    #   "groq"   -> Groq Whisper only (English-only deployments)
    # Uses gemini_utility_model (flash-lite) for cheap, quick transcription.
    stt_provider: str = "gemini"
    
    # Retrieval
    retrieval_top_k: int = 10  # Increased from 6 to 10 for more comprehensive answers
    
    # Distance-based Relevance Filtering
    enable_distance_filtering: bool = True  # Filter out irrelevant results based on distance threshold
    distance_threshold: float = 1.3  # L2 distance threshold (0=identical, 2=very different)
    min_results_after_filter: int = 1  # Minimum results to return even if below threshold
    # If even the BEST match is farther than this, treat the query as out-of-scope
    # and inject NO context (prevents answering off-topic questions with a random doc).
    hard_distance_cutoff: float = 1.55
    
    # Advanced Retrieval Features (ordered by cost-effectiveness)
    use_query_normalization: bool = True  # NEW - lightweight, enabled by default
    use_hybrid_search: bool = True
    use_reranking: bool = True
    use_query_rewriting: bool = False  # More expensive alternative to normalization
    use_hyde: bool = False
    use_multi_query: bool = False  # Most comprehensive but expensive - use for critical queries
    multi_query_variations: int = 3
    multi_query_boost_original: float = 1.5

    # Cross-encoder re-ranking model. The old ms-marco-MiniLM is English-only and
    # would REORDER Sinhala results wrongly, so we default to a multilingual
    # cross-encoder (XLM-R based, same light ~470MB footprint). For best Sinhala
    # quality at the cost of size/speed, use "BAAI/bge-reranker-v2-m3". Set
    # use_reranking=False to skip re-ranking entirely if it hurts.
    rerank_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    
    # Semantic Metadata Settings
    enable_semantic_metadata: bool = True  # Embed metadata values into searchable content
    metadata_synonym_expansion: bool = True  # Add synonyms for metadata values
    semantic_metadata_position: str = "prefix"  # "prefix", "suffix", or "both"
    
    # WhatsApp Integration
    whatsapp_verify_token: str = "your_verify_token_here"
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_client_id: str = "customer_care_test"  # Default client for WhatsApp
    whatsapp_bot_name: str = "Customer Support"
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Set ChromaDB directory if not specified
        if self.chromadb_persist_directory is None:
            self.chromadb_persist_directory = str(self.vector_stores_dir / "chromadb")
        
        # Ensure directories exist
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.vector_stores_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment.lower() == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment.lower() == "development"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """
    Get the global settings instance.
    
    Returns:
        Settings instance
    """
    return settings
