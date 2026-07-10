"""
Advanced Retrieval Optimization Service

Implements:
1. Hybrid Search (Vector + BM25 Keyword Search)
2. Re-Ranking with Cross-Encoder
3. Query Transformation (Rewriting + HyDE)

High ROI features for customer support RAG accuracy.
"""

import logging
from typing import List, Dict, Optional, Any, Tuple
import numpy as np
from rank_bm25 import BM25Okapi
import re

from logger import get_logger
from config import get_settings

logger = get_logger(__name__)


class RetrievalOptimizer:
    """
    Advanced retrieval optimization for RAG systems.
    
    Features:
    - Hybrid Search: Combines vector similarity with keyword matching
    - Re-Ranking: Deep scoring of candidates with cross-encoder
    - Query Transformation: Rewrites queries for better retrieval
    """
    
    def __init__(
        self,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
        enable_reranking: bool = True,
        rerank_model: Optional[str] = None
    ):
        """
        Initialize the retrieval optimizer.
        
        Args:
            vector_weight: Weight for vector search scores (0-1)
            keyword_weight: Weight for keyword search scores (0-1)
            enable_reranking: Whether to use cross-encoder re-ranking
            rerank_model: HuggingFace model for re-ranking
        """
        self.vector_weight = vector_weight
        self.keyword_weight = keyword_weight
        self.enable_reranking = enable_reranking
        
        # Validate weights
        if abs((vector_weight + keyword_weight) - 1.0) > 0.01:
            logger.warning(f"Weights don't sum to 1.0: {vector_weight} + {keyword_weight}")
        
        # Resolve the re-ranker model (multilingual by default, see config).
        if rerank_model is None:
            rerank_model = get_settings().rerank_model

        # Initialize re-ranker if enabled
        self.reranker = None
        if enable_reranking:
            try:
                from sentence_transformers import CrossEncoder
                self.reranker = CrossEncoder(rerank_model)
                logger.info(f"Initialized re-ranker: {rerank_model}")
            except Exception as e:
                logger.error(f"Failed to load re-ranker: {e}")
                self.enable_reranking = False
        
        # BM25 index (will be built per collection)
        self.bm25_indexes: Dict[str, BM25Okapi] = {}
        self.bm25_documents: Dict[str, List[str]] = {}
        self.bm25_doc_ids: Dict[str, List[int]] = {}
        
        logger.info(f"RetrievalOptimizer initialized (vector={vector_weight}, keyword={keyword_weight})")
    
    def build_bm25_index(
        self,
        collection_name: str,
        documents: List[str],
        doc_ids: Optional[List[int]] = None
    ) -> None:
        """
        Build BM25 keyword search index for a collection.
        
        Args:
            collection_name: Name of the collection
            documents: List of document texts
            doc_ids: Optional document IDs (for tracking)
        """
        # Tokenize documents
        tokenized_docs = [self._tokenize(doc) for doc in documents]
        
        # Build BM25 index
        bm25 = BM25Okapi(tokenized_docs)
        
        # Store
        self.bm25_indexes[collection_name] = bm25
        self.bm25_documents[collection_name] = documents
        self.bm25_doc_ids[collection_name] = doc_ids or list(range(len(documents)))
        
        logger.info(f"Built BM25 index for '{collection_name}': {len(documents)} documents")
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Simple tokenization for BM25.
        
        Args:
            text: Text to tokenize
            
        Returns:
            List of tokens
        """
        # Lowercase and split on non-alphanumeric
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        return tokens
    
    def hybrid_search(
        self,
        collection_name: str,
        query: str,
        vector_results: List[Dict[str, Any]],
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Hybrid Search: Combine vector search with BM25 keyword search.
        
        Strategy:
        1. Get vector search results (already provided)
        2. Get BM25 keyword search results
        3. Fuse scores with weighted combination
        4. Return top-k by combined score
        
        Args:
            collection_name: Name of the collection
            query: User query
            vector_results: Results from vector search with 'distance' scores
            top_k: Number of results to return
            
        Returns:
            Hybrid search results sorted by combined score
        """
        if collection_name not in self.bm25_indexes:
            logger.warning(f"No BM25 index for '{collection_name}', returning vector results")
            return vector_results[:top_k]
        
        # Get BM25 results
        bm25_results = self._bm25_search(collection_name, query, top_k=50)
        
        # Normalize and combine scores
        combined = self._fuse_results(vector_results, bm25_results)
        
        # Sort by combined score (higher is better)
        combined.sort(key=lambda x: x['hybrid_score'], reverse=True)
        
        return combined[:top_k]
    
    def _bm25_search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Perform BM25 keyword search.
        
        Args:
            collection_name: Collection name
            query: Search query
            top_k: Number of results
            
        Returns:
            List of results with BM25 scores
        """
        bm25 = self.bm25_indexes[collection_name]
        documents = self.bm25_documents[collection_name]
        doc_ids = self.bm25_doc_ids[collection_name]
        
        # Tokenize query
        query_tokens = self._tokenize(query)
        
        # Get BM25 scores
        scores = bm25.get_scores(query_tokens)
        
        # Create results
        results = []
        for idx, (score, doc_id) in enumerate(zip(scores, doc_ids)):
            if score > 0:  # Only include matches
                results.append({
                    'doc_id': doc_id,
                    'text': documents[idx],
                    'bm25_score': float(score),
                    'index': idx
                })
        
        # Sort by BM25 score
        results.sort(key=lambda x: x['bm25_score'], reverse=True)
        
        return results[:top_k]
    
    def _normalize_scores(self, scores: List[float]) -> List[float]:
        """
        Min-max normalize scores to 0-1 range.
        
        Args:
            scores: List of scores
            
        Returns:
            Normalized scores
        """
        if not scores or len(scores) == 1:
            return [1.0] * len(scores)
        
        min_score = min(scores)
        max_score = max(scores)
        
        if max_score == min_score:
            return [1.0] * len(scores)
        
        return [(s - min_score) / (max_score - min_score) for s in scores]
    
    def _fuse_results(
        self,
        vector_results: List[Dict[str, Any]],
        bm25_results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Fuse vector and BM25 results with weighted combination.
        
        Uses Reciprocal Rank Fusion (RRF) + score normalization.
        
        Args:
            vector_results: Results from vector search
            bm25_results: Results from BM25 search
            
        Returns:
            Combined results with hybrid scores
        """
        # Build document map
        doc_map = {}
        
        # Add vector results (convert distance to similarity)
        vector_distances = [r.get('distance', 0) for r in vector_results]
        if vector_distances:
            # Convert L2 distance to similarity (lower distance = higher similarity)
            max_dist = max(vector_distances) if vector_distances else 1.0
            vector_scores = [1 - (d / (max_dist + 1e-6)) for d in vector_distances]
            norm_vector_scores = self._normalize_scores(vector_scores)
        else:
            norm_vector_scores = []
        
        for idx, (result, norm_score) in enumerate(zip(vector_results, norm_vector_scores)):
            doc_id = result.get('doc_id', idx)
            doc_map[doc_id] = {
                **result,
                'vector_score': norm_score,
                'bm25_score': 0.0,
                'vector_rank': idx + 1
            }
        
        # Add BM25 results
        bm25_scores = [r['bm25_score'] for r in bm25_results]
        norm_bm25_scores = self._normalize_scores(bm25_scores)
        
        for idx, (result, norm_score) in enumerate(zip(bm25_results, norm_bm25_scores)):
            doc_id = result.get('doc_id', result.get('index'))
            
            if doc_id in doc_map:
                # Document found in both - update BM25 score
                doc_map[doc_id]['bm25_score'] = norm_score
                doc_map[doc_id]['bm25_rank'] = idx + 1
            else:
                # Document only in BM25 results
                doc_map[doc_id] = {
                    **result,
                    'vector_score': 0.0,
                    'bm25_score': norm_score,
                    'bm25_rank': idx + 1
                }
        
        # Compute hybrid scores
        for doc_id, doc in doc_map.items():
            vector_score = doc.get('vector_score', 0)
            bm25_score = doc.get('bm25_score', 0)
            
            # Weighted combination
            hybrid_score = (
                self.vector_weight * vector_score +
                self.keyword_weight * bm25_score
            )
            
            # Add RRF bonus (Reciprocal Rank Fusion)
            vector_rank = doc.get('vector_rank', 1000)
            bm25_rank = doc.get('bm25_rank', 1000)
            rrf_score = (1 / (60 + vector_rank)) + (1 / (60 + bm25_rank))
            
            # Final hybrid score
            doc['hybrid_score'] = hybrid_score + (0.1 * rrf_score)  # Small RRF boost
            doc['fusion_method'] = 'weighted_rrf'
        
        return list(doc_map.values())
    
    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Re-rank documents using Cross-Encoder for deep scoring.
        
        Strategy:
        1. Take top N candidates from initial retrieval (e.g., 50)
        2. Use Cross-Encoder to score each candidate deeply
        3. Return top-k by cross-encoder score
        
        This is expensive but highly accurate - use after fast retrieval.
        
        Args:
            query: User query
            documents: Candidate documents from initial retrieval
            top_k: Number of final results
            
        Returns:
            Re-ranked documents
        """
        if not self.enable_reranking or self.reranker is None:
            logger.warning("Re-ranking not available, returning original order")
            return documents[:top_k]
        
        if not documents:
            return []
        
        try:
            # Prepare query-document pairs
            pairs = [(query, doc.get('text', '')) for doc in documents]
            
            # Get cross-encoder scores
            scores = self.reranker.predict(pairs)
            
            # Add scores to documents
            for doc, score in zip(documents, scores):
                doc['rerank_score'] = float(score)
                # Keep original scores for debugging
                doc['original_hybrid_score'] = doc.get('hybrid_score', doc.get('distance', 0))
            
            # Sort by rerank score
            documents.sort(key=lambda x: x['rerank_score'], reverse=True)
            
            logger.info(f"Re-ranked {len(documents)} documents, returning top {top_k}")
            
            return documents[:top_k]
            
        except Exception as e:
            logger.error(f"Re-ranking failed: {e}")
            return documents[:top_k]
    
    def rewrite_query(
        self,
        query: str,
        llm_service: Any,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Query Rewriting: Transform user's messy query into precise search query.
        
        Examples:
        - "help cant login" → "steps to troubleshoot login failure"
        - "Error 505" → "error code 505 meaning and troubleshooting"
        - "refund???" → "refund policy and process"
        
        Args:
            query: User's raw query
            llm_service: LLM service for rewriting
            conversation_history: Optional conversation context
            
        Returns:
            Rewritten search query
        """
        system_prompt = """You are a query optimization expert. Rewrite user queries to be clear, precise search queries.

Rules:
1. Fix typos and grammar
2. Add relevant search keywords
3. Make specific (e.g., "login error" → "steps to troubleshoot login authentication failure")
4. Keep it concise (under 20 words)
5. Preserve important specifics (error codes, product names, SKUs, etc.)
6. Remove filler words ("help", "please", "???")

Examples:
User: "help cant login"
Rewritten: "troubleshoot login authentication failure steps"

User: "Error 505 what mean"
Rewritten: "error code 505 meaning and resolution"

User: "refund???"
Rewritten: "refund policy process and requirements"

Now rewrite this query:"""
        
        try:
            # Use LLM to rewrite
            rewritten = llm_service.generate_response(
                query=f"Original query: {query}\n\nRewritten query:",
                system_prompt=system_prompt,
                conversation_history=conversation_history
            )
            
            # Clean up response
            rewritten = rewritten.strip().strip('"').strip("'")
            
            # Safety check: if rewrite is too different or empty, use original
            if not rewritten or len(rewritten) > len(query) * 3:
                logger.warning(f"Query rewrite suspicious, using original. Original: '{query}', Rewritten: '{rewritten}'")
                return query
            
            logger.info(f"Query rewritten: '{query}' → '{rewritten}'")
            return rewritten
            
        except Exception as e:
            logger.error(f"Query rewriting failed: {e}")
            return query
    
    def normalize_and_enhance_query(
        self,
        query: str,
        llm_service: Any,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        domain_context: Optional[str] = None
    ) -> str:
        """
        Smart Query Normalization & Enhancement - Lightweight alternative to multi-query.
        
        Cost-efficient preprocessing that handles:
        1. Text normalization (lowercase, clean punctuation)
        2. Abbreviation expansion (ol → Ordinary Level, al → Advanced Level)
        3. Typo correction (experiance → experience)
        4. Semantic expansion (add synonyms for better embedding match)
        5. Domain-specific term conversion
        
        This is 6-10x cheaper than multi-query fusion while handling 80% of the same issues.
        
        Examples:
        - "where did he did his ol" → 
          "Where did the person complete Ordinary Level (O/L) education?"
        
        - "what r his qualifications" → 
          "What are the person's educational qualifications and certifications?"
        
        - "tell me bout work experiance" → 
          "Tell me about the person's work experience and employment history"
        
        Args:
            query: Raw user query
            llm_service: LLM service for normalization
            conversation_history: Optional conversation context for pronoun resolution
            domain_context: Optional domain info (e.g., "Sri Lankan education system CV")
            
        Returns:
            Normalized and enhanced query
        """
        # Build context from conversation history
        context = ""
        if conversation_history:
            recent = conversation_history[-2:] if len(conversation_history) >= 2 else conversation_history
            context = "\n\nRecent conversation:\n" + "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')[:100]}" 
                for msg in recent
            ])
        
        domain_info = f"\n\nDomain context: {domain_context}" if domain_context else ""
        
        # The knowledge base is embedded in ONE language; the search query must be
        # in that SAME language or the multilingual embeddings won't match (cross-
        # language similarity is weak). So we convert every query to the KB language
        # here. NOTE: this only affects RETRIEVAL — the answer is still generated
        # from the user's ORIGINAL question, so the reply stays in their language.
        content_language = (get_settings().content_language or "english").lower()
        if content_language == "english":
            # English-pivot: documents are in English -> translate ALL queries
            # (native Sinhala script AND romanized "Singlish") into clear English.
            language_block = (
                "• The documents are in ENGLISH. Convert the query to clear English:\n"
                "  - Sinhala script (සිංහල)  -> translate to English.\n"
                "  - Tamil script (தமிழ்)  -> translate to English.\n"
                "  - Romanized Sinhala / \"Singlish\" (Sinhala typed in English letters,\n"
                "    e.g. \"mata refund ekak ganna puluwanda\") -> translate to English.\n"
                "  - Romanized Tamil (Tamil typed in English letters) -> translate to English.\n"
                "  - Already English -> keep as English and just clean it up.\n"
                "• Examples:\n"
                "  \"mata refund ekak ganna puluwanda\" -> \"Can I get a refund?\"\n"
                "  \"ඔයාලගේ office එක කීයටද වහන්නේ?\" -> \"What time does your office close?\"\n"
                "  \"உங்கள் அலுவலகம் எத்தனை மணிக்கு மூடப்படும்?\" -> \"What time does your office close?\"\n"
                "  \"plan eka mokakda\" -> \"What plans are available?\""
            )
        elif content_language == "auto":
            language_block = (
                "• Leave the query in whatever language/script it arrived in; only fix\n"
                "  obvious typos. Do not translate between languages."
            )
        else:  # "sinhala": documents are in Sinhala script
            language_block = (
                "• The documents are in SINHALA SCRIPT. Convert the query to Sinhala script:\n"
                "  - Romanized Sinhala / \"Singlish\" (e.g. \"oyage plan eka mokakda\")\n"
                "    -> transliterate/translate into natural Sinhala script.\n"
                "  - Already Sinhala script -> keep it and just clean it up.\n"
                "  - Plain English -> keep it in English.\n"
                "• Examples:\n"
                "  \"oyage plan eka mokakda\" -> \"දැනට තියෙන ප්ලෑන් මොනවද?\"\n"
                "  \"mata refund ekak ganna puluwanda\" -> \"මට මුදල් ආපසු ලබා ගත හැකිද?\""
            )

        system_prompt = f"""You are a query normalization expert. Transform raw user queries into clear, comprehensive search queries optimized for semantic retrieval.

🌐 LANGUAGE (do this FIRST, it matters most for retrieval):
{language_block}
• Output ONLY the query — no explanation, no romanization in brackets.

🎯 Your Tasks (then apply to the converted query):
1. **Fix typos and grammar** - Correct spelling and grammatical errors
2. **Expand abbreviations** - Convert short forms to full terms
   • ol, O/L, o-level → "Ordinary Level (O/L)"  
   • al, A/L, a-level → "Advanced Level (A/L)"
   • cv → "curriculum vitae resume"
   • uni → "university"
3. **Resolve pronouns** - Replace "he/she/his/her" with "the person" or specific context
4. **Add semantic richness** - Include synonyms and related terms
5. **Preserve specifics** - Keep numbers, dates, names, locations
6. **Keep natural** - Should read like a proper question, not keyword soup

🌍 Domain-Specific Rules:{domain_info}
• Recognize education terms: GCE, qualification levels, examination boards
• Recognize CV sections: education, work experience, skills, volunteering
• Keep cultural context (e.g., Sri Lankan exam system)

✅ Good Examples:

Input: "where did he did his ol"
Output: "Where did the person complete their Ordinary Level (O/L) education?"

Input: "what r his qualifications"  
Output: "What are the person's educational qualifications and certifications?"

Input: "tell me bout work experiance"
Output: "Tell me about the person's work experience and employment history"

Input: "his al results plz"
Output: "What are the person's Advanced Level (A/L) examination results?"

❌ Avoid:
• Keyword lists: "person education O/L results qualification" 
• Over-expansion: Adding unnecessary details
• Removing important context
• Changing the core question meaning

Return ONLY the normalized query, nothing else.{context}"""

        prompt = f"Raw query: {query}\n\nNormalized query:"
        
        try:
            # Generate normalized query using the cheap utility model
            normalized = llm_service.generate_response(
                query=prompt,
                system_prompt=system_prompt,
                lightweight=True
            )
            
            # Clean up response
            normalized = normalized.strip().strip('"').strip("'").strip()
            
            # Safety check: if normalization fails or is too different, use original
            if not normalized or len(normalized) > len(query) * 4:
                logger.warning(f"Query normalization suspicious, using original. "
                              f"Original: '{query}', Normalized: '{normalized}'")
                return query
            
            # Additional safety: ensure it's still a question-like format
            if len(normalized) < 5:
                logger.warning(f"Normalized query too short: '{normalized}', using original")
                return query
            
            logger.info(f"Query normalized: '{query}' → '{normalized}'")
            return normalized
            
        except Exception as e:
            logger.error(f"Query normalization failed: {e}")
            return query
    
    def hyde_query(
        self,
        query: str,
        llm_service: Any,
        system_role: str = "helpful assistant"
    ) -> str:
        """
        HyDE (Hypothetical Document Embeddings): Generate ideal answer, search for similar docs.
        
        Strategy:
        1. Use LLM to generate a hypothetical "perfect answer" to the query
        2. Embed this hypothetical answer
        3. Search for real documents that look like this perfect answer
        4. Real docs that match the hypothetical answer are likely relevant
        
        This helps bridge the query-document gap (queries are questions, docs are answers).
        
        Args:
            query: User's question
            llm_service: LLM service for generation
            system_role: System role for LLM
            
        Returns:
            Hypothetical answer (to be embedded and searched)
        """
        system_prompt = f"""You are a {system_role}. Generate a detailed, comprehensive answer to the user's question as if you had perfect knowledge.

IMPORTANT:
- Write as if answering from documentation
- Include specific details, steps, explanations
- Use technical terms and keywords that would appear in real docs
- Keep it factual and structured (2-3 paragraphs, 100-200 words)
- This is NOT for the user - it's a search template

The goal is to create an "ideal document" that real documentation should resemble."""
        
        try:
            # Generate hypothetical answer
            hypothetical = llm_service.generate_response(
                query=query,
                system_prompt=system_prompt
            )
            
            logger.info(f"Generated HyDE answer ({len(hypothetical)} chars) for query: {query[:50]}...")
            logger.debug(f"HyDE result: {hypothetical[:200]}...")
            
            return hypothetical
            
        except Exception as e:
            logger.error(f"HyDE generation failed: {e}")
            # Fallback: use original query
            return query
    
    def generate_query_variations(
        self,
        query: str,
        llm_service: Any,
        num_variations: int = 3,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> List[str]:
        """
        Generate multiple query variations for RAG Fusion.
        
        Strategy:
        1. Use LLM to generate diverse reformulations of the query
        2. Cover different phrasings, abbreviations, synonyms
        3. Each variation searches from a different angle
        4. Results are fused using Reciprocal Rank Fusion (RRF)
        
        This makes retrieval robust to:
        - Typos and grammatical errors
        - Domain-specific abbreviations (e.g., "ol" vs "O/L")
        - Different terminology across clients
        - Ambiguous or vague questions
        
        Args:
            query: Original user query
            llm_service: LLM service for generation
            num_variations: Number of variations to generate (default: 3)
            conversation_history: Optional conversation context
            
        Returns:
            List of query variations (includes original query)
        """
        system_prompt = f"""Generate {num_variations} different ways to ask the following query. Each variation should:

1. Rephrase using different words/synonyms
2. Expand abbreviations (e.g., "ol" → "Ordinary Level O/L", "al" → "Advanced Level A/L")
3. Fix any typos or grammatical errors
4. Cover different aspects of the question
5. Be clear and specific

Return ONLY the {num_variations} reformulated queries, one per line, numbered.
Do NOT include explanations or extra text."""

        context = ""
        if conversation_history:
            recent = conversation_history[-2:] if len(conversation_history) >= 2 else conversation_history
            context = "\n\nRecent conversation context:\n" + "\n".join([
                f"{msg.get('role', 'user')}: {msg.get('content', '')}" 
                for msg in recent
            ])
        
        prompt = f"Original query: {query}{context}\n\nGenerate {num_variations} variations:"
        
        try:
            # Generate variations
            response = llm_service.generate_response(
                query=prompt,
                system_prompt=system_prompt
            )
            
            # Parse variations (expecting numbered lines)
            lines = [line.strip() for line in response.split('\n') if line.strip()]
            variations = []
            
            for line in lines:
                # Remove numbering (e.g., "1.", "1)", "1 -", etc.)
                clean_line = re.sub(r'^\d+[\.\)\-\:]\s*', '', line).strip()
                # Remove quotes if present
                clean_line = clean_line.strip('"').strip("'").strip()
                
                if clean_line and len(clean_line) > 5:  # Sanity check
                    variations.append(clean_line)
            
            # Deduplicate and limit
            unique_variations = []
            seen = set()
            for var in variations:
                var_lower = var.lower()
                if var_lower not in seen:
                    unique_variations.append(var)
                    seen.add(var_lower)
            
            # Ensure we have at least original query
            if query.lower() not in seen:
                unique_variations.insert(0, query)
            
            # Limit to requested number + original
            final_variations = unique_variations[:num_variations + 1]
            
            logger.info(f"Generated {len(final_variations)} query variations for: '{query[:50]}...'")
            for i, var in enumerate(final_variations):
                logger.debug(f"  Variation {i}: {var}")
            
            return final_variations
            
        except Exception as e:
            logger.error(f"Query variation generation failed: {e}")
            # Fallback: return original query only
            return [query]
    
    def multi_query_retrieval(
        self,
        query: str,
        vector_store: Any,
        embeddings_service: Any,
        llm_service: Any,
        collection_name: str,
        num_variations: int = 3,
        top_k_per_query: int = 10,
        final_top_k: int = 6,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
        boost_original: float = 1.5
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Multi-Query RAG Fusion: Retrieve using multiple query variations and fuse results.
        
        Pipeline:
        1. Generate N query variations
        2. Retrieve top-k documents for each variation
        3. Fuse results using Reciprocal Rank Fusion (RRF)
        4. Return top-ranked unique documents
        
        Args:
            query: Original user query
            vector_store: Vector store instance
            embeddings_service: Embeddings service
            llm_service: LLM service for generating variations
            collection_name: Collection to search
            num_variations: Number of query variations (default: 3)
            top_k_per_query: Documents per variation (default: 10)
            final_top_k: Final number of results (default: 6)
            conversation_history: Optional conversation context
            metadata_filter: Optional metadata filtering
            boost_original: Weight boost for original query (default: 1.5x)
            
        Returns:
            Tuple of (fused results, metadata)
        """
        metadata = {
            'original_query': query,
            'num_variations': 0,
            'variations': [],
            'retrievals': [],
            'fusion_method': 'reciprocal_rank_fusion'
        }
        
        try:
            # Step 1: Generate query variations
            variations = self.generate_query_variations(
                query=query,
                llm_service=llm_service,
                num_variations=num_variations,
                conversation_history=conversation_history
            )
            
            metadata['num_variations'] = len(variations)
            metadata['variations'] = variations
            
            # Step 2: Retrieve for each variation
            all_results = []
            
            for i, var_query in enumerate(variations):
                try:
                    # Generate embedding
                    query_embedding = embeddings_service.embed_text(var_query)
                    
                    # Vector search — FAISS store returns a ChromaDB-style dict
                    # ({documents/metadatas/distances/ids: [[...]]}); normalize it
                    # into a flat list of doc dicts for RRF fusion.
                    raw = vector_store.query(
                        collection_name=collection_name,
                        query_embeddings=[query_embedding],
                        n_results=top_k_per_query,
                        metadata_filter=metadata_filter
                    )
                    results = []
                    if raw and raw.get('documents') and raw['documents'][0]:
                        docs = raw['documents'][0]
                        metas = (raw.get('metadatas') or [[]])[0]
                        dists = (raw.get('distances') or [[]])[0]
                        ids = (raw.get('ids') or [[]])[0]
                        for d_i, text in enumerate(docs):
                            results.append({
                                'id': ids[d_i] if d_i < len(ids) else f"doc_{d_i}",
                                'text': text,
                                'metadata': metas[d_i] if d_i < len(metas) else {},
                                'distance': float(dists[d_i]) if d_i < len(dists) else None,
                            })

                    # Track which variation this came from
                    is_original = (i == 0 and var_query.lower() == query.lower())
                    weight = boost_original if is_original else 1.0
                    
                    all_results.append({
                        'variation': var_query,
                        'is_original': is_original,
                        'weight': weight,
                        'results': results,
                        'count': len(results)
                    })
                    
                    metadata['retrievals'].append({
                        'variation': var_query,
                        'num_results': len(results),
                        'is_original': is_original
                    })
                    
                    logger.debug(f"Retrieved {len(results)} docs for variation {i}: '{var_query[:50]}...'")
                    
                except Exception as e:
                    logger.error(f"Retrieval failed for variation '{var_query}': {e}")
                    continue
            
            if not all_results:
                logger.error("No successful retrievals for any variation")
                return [], metadata
            
            # Step 3: Fuse results using RRF
            fused_results = self._fuse_multi_query_results(
                all_results=all_results,
                top_k=final_top_k
            )
            
            metadata['num_fused_results'] = len(fused_results)
            metadata['num_unique_docs'] = len(set(r.get('id') for r in fused_results if r.get('id')))
            
            logger.info(f"Multi-query fusion: {len(variations)} variations → {len(fused_results)} results")
            
            return fused_results, metadata
            
        except Exception as e:
            logger.error(f"Multi-query retrieval failed: {e}")
            return [], metadata
    
    def _fuse_multi_query_results(
        self,
        all_results: List[Dict[str, Any]],
        top_k: int = 6,
        rrf_k: int = 60
    ) -> List[Dict[str, Any]]:
        """
        Fuse multiple retrieval results using Reciprocal Rank Fusion (RRF).
        
        RRF Formula: score(doc) = Σ weight_i / (k + rank_i)
        where k=60 is a constant that reduces impact of high ranks
        
        Args:
            all_results: List of retrieval results from different queries
            top_k: Number of top results to return
            rrf_k: RRF constant (default: 60, standard value)
            
        Returns:
            Fused and ranked results
        """
        # Document ID → {doc data, scores, ranks}
        doc_map = {}
        
        # Process each query's results
        for query_result in all_results:
            results = query_result.get('results', [])
            weight = query_result.get('weight', 1.0)
            
            for rank, doc in enumerate(results):
                doc_id = doc.get('id') or doc.get('metadata', {}).get('id', f"doc_{rank}")
                
                # Initialize document if new
                if doc_id not in doc_map:
                    doc_map[doc_id] = {
                        'doc': doc,
                        'rrf_score': 0.0,
                        'appearances': 0,
                        'ranks': [],
                        'original_scores': []
                    }
                
                # RRF score: weight / (k + rank)
                # rank is 0-indexed, so rank 0 = top result
                rrf_contribution = weight / (rrf_k + rank)
                
                doc_map[doc_id]['rrf_score'] += rrf_contribution
                doc_map[doc_id]['appearances'] += 1
                doc_map[doc_id]['ranks'].append(rank)
                
                # Store original score if available
                if 'score' in doc or 'distance' in doc:
                    original_score = doc.get('score', 1.0 - doc.get('distance', 0.5))
                    doc_map[doc_id]['original_scores'].append(original_score)
        
        # Convert to list and add fusion metadata
        fused_docs = []
        for doc_id, doc_data in doc_map.items():
            doc = doc_data['doc'].copy()
            
            # Add fusion score
            doc['fusion_score'] = doc_data['rrf_score']
            doc['score'] = doc_data['rrf_score']  # Use fusion score as primary score
            
            # Add metadata about fusion
            if 'metadata' not in doc:
                doc['metadata'] = {}
            
            doc['metadata']['fusion_appearances'] = doc_data['appearances']
            doc['metadata']['fusion_avg_rank'] = sum(doc_data['ranks']) / len(doc_data['ranks'])
            doc['metadata']['fusion_best_rank'] = min(doc_data['ranks'])
            
            fused_docs.append(doc)
        
        # Sort by fusion score (higher is better)
        fused_docs.sort(key=lambda x: x.get('fusion_score', 0), reverse=True)
        
        # Return top-k
        top_results = fused_docs[:top_k]
        
        logger.debug(f"Fused {len(doc_map)} unique documents, returning top {len(top_results)}")
        for i, doc in enumerate(top_results[:3]):  # Log top 3
            logger.debug(f"  Rank {i+1}: score={doc.get('fusion_score', 0):.4f}, "
                        f"appearances={doc.get('metadata', {}).get('fusion_appearances', 0)}")
        
        return top_results
    
    def optimize_retrieval(
        self,
        collection_name: str,
        query: str,
        vector_results: List[Dict[str, Any]],
        llm_service: Any,
        use_hybrid: bool = True,
        use_reranking: bool = True,
        use_query_rewriting: bool = True,
        use_hyde: bool = False,
        top_k_initial: int = 50,
        top_k_final: int = 5,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Complete retrieval optimization pipeline.
        
        Full pipeline:
        1. Query Transformation (rewriting or HyDE)
        2. Hybrid Search (vector + BM25)
        3. Re-Ranking (cross-encoder)
        
        Args:
            collection_name: Collection name
            query: User query
            vector_results: Initial vector search results
            llm_service: LLM service
            use_hybrid: Enable hybrid search
            use_reranking: Enable re-ranking
            use_query_rewriting: Enable query rewriting
            use_hyde: Enable HyDE (mutually exclusive with rewriting)
            top_k_initial: Candidates for re-ranking
            top_k_final: Final number of results
            conversation_history: Optional conversation context
            
        Returns:
            Tuple of (optimized results, optimization metadata)
        """
        metadata = {
            'original_query': query,
            'transformations': [],
            'num_initial_results': len(vector_results)
        }
        
        # Step 1: Query Transformation
        search_query = query
        
        if use_hyde:
            # HyDE: Generate hypothetical answer
            search_query = self.hyde_query(query, llm_service)
            metadata['transformations'].append('hyde')
            metadata['hyde_query'] = search_query[:200] + '...'
            
        elif use_query_rewriting:
            # Query Rewriting: Clean up query
            search_query = self.rewrite_query(query, llm_service, conversation_history)
            metadata['transformations'].append('rewriting')
            metadata['rewritten_query'] = search_query
        
        # Step 2: Hybrid Search
        if use_hybrid:
            results = self.hybrid_search(
                collection_name,
                search_query,
                vector_results,
                top_k=top_k_initial
            )
            metadata['transformations'].append('hybrid_search')
            metadata['num_after_hybrid'] = len(results)
        else:
            results = vector_results[:top_k_initial]
        
        # Step 3: Re-Ranking
        if use_reranking and len(results) > top_k_final:
            # Use ORIGINAL query for re-ranking (not transformed one)
            results = self.rerank(query, results, top_k=top_k_final)
            metadata['transformations'].append('reranking')
            metadata['num_after_rerank'] = len(results)
        else:
            results = results[:top_k_final]
        
        metadata['final_count'] = len(results)
        
        logger.info(f"Retrieval optimization complete: {metadata['transformations']}")
        
        return results, metadata
