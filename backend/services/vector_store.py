"""
Vector Store Service for RAG System
Handles storing and retrieving document embeddings using FAISS
"""

import logging
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path
import json
import pickle
import numpy as np
import faiss

from logger import get_logger
from config import get_settings

logger = get_logger(__name__)
settings = get_settings()


class VectorStoreService:
    """
    Service for managing document embeddings in a vector database.
    Uses FAISS for efficient similarity search with persistent storage.
    """
    
    def __init__(self, persist_directory: Optional[str] = None):
        """
        Initialize the Vector Store Service.
        
        Args:
            persist_directory: Directory path for persistent storage.
                             If None, uses the default from settings.
        """
        self.persist_directory = persist_directory or str(settings.vector_stores_dir / "faiss")
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)
        
        # Dictionary to store FAISS indexes for different collections
        self.collections: Dict[str, Dict[str, Any]] = {}
        
        logger.info(f"VectorStoreService initialized with persist_directory: {self.persist_directory}")
    
    def create_collection(
        self,
        name: str,
        embedding_dimension: Optional[int] = None
    ) -> None:
        """
        Create a new collection for storing embeddings.
        
        Args:
            name: Name of the collection
            embedding_dimension: Dimension of embeddings (default from settings)
        """
        if name in self.collections:
            logger.warning(f"Collection '{name}' already exists")
            return
        
        dimension = embedding_dimension or settings.embedding_dimension
        
        # Create FAISS index (L2 distance)
        index = faiss.IndexFlatL2(dimension)
        
        self.collections[name] = {
            'index': index,
            'documents': [],
            'metadatas': [],
            'ids': [],
            'dimension': dimension
        }
        
        logger.info(f"Created collection '{name}' with dimension {dimension}")
    
    def add_documents(
        self,
        collection_name: str,
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None
    ) -> None:
        """
        Add documents with their embeddings to a collection.
        
        Args:
            collection_name: Name of the collection
            documents: List of document texts
            embeddings: List of embedding vectors
            metadatas: Optional list of metadata dictionaries
            ids: Optional list of document IDs (auto-generated if not provided)
        """
        if collection_name not in self.collections:
            raise ValueError(f"Collection '{collection_name}' does not exist")
        
        collection = self.collections[collection_name]
        
        # Convert embeddings to numpy array
        embeddings_array = np.array(embeddings, dtype=np.float32)
        
        # Validate embedding dimension
        if embeddings_array.shape[1] != collection['dimension']:
            raise ValueError(
                f"Embedding dimension {embeddings_array.shape[1]} "
                f"does not match collection dimension {collection['dimension']}"
            )
        
        # Generate IDs if not provided
        if ids is None:
            start_id = len(collection['ids'])
            ids = [f"doc_{start_id + i}" for i in range(len(documents))]
        
        # Prepare metadatas
        if metadatas is None:
            metadatas = [{} for _ in documents]
        
        # Add to FAISS index
        collection['index'].add(embeddings_array)
        
        # Store documents, metadatas, and ids
        collection['documents'].extend(documents)
        collection['metadatas'].extend(metadatas)
        collection['ids'].extend(ids)
        
        logger.info(f"Added {len(documents)} documents to collection '{collection_name}'")
    
    def query(
        self,
        collection_name: str,
        query_embeddings: List[List[float]],
        n_results: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Query the collection for similar documents with optional metadata filtering.
        
        Args:
            collection_name: Name of the collection to query
            query_embeddings: List of query embedding vectors
            n_results: Number of results to return per query
            metadata_filter: Optional dictionary to filter results by metadata
                           Example: {"section": "work_experience", "user_tier": "enterprise"}
        
        Returns:
            Dictionary containing:
                - documents: List of lists of matching document texts
                - metadatas: List of lists of metadata dictionaries
                - distances: List of lists of distance scores
                - ids: List of lists of document IDs
        """
        if collection_name not in self.collections:
            raise ValueError(f"Collection '{collection_name}' does not exist")
        
        collection = self.collections[collection_name]
        
        # Convert query embeddings to numpy array
        query_array = np.array(query_embeddings, dtype=np.float32)
        
        # Validate dimension
        if query_array.shape[1] != collection['dimension']:
            raise ValueError(
                f"Query embedding dimension {query_array.shape[1]} "
                f"does not match collection dimension {collection['dimension']}"
            )
        
        # If metadata filter provided, retrieve more results and filter
        search_n = n_results * 3 if metadata_filter else n_results
        
        # Perform search
        distances, indices = collection['index'].search(query_array, min(search_n, len(collection['documents'])))
        
        # Prepare results
        results = {
            'documents': [],
            'metadatas': [],
            'distances': [],
            'ids': [],
            # True positional index of each match within collection['documents']/
            # ['metadatas'] - the same index space BM25 uses for its doc_ids
            # (list(range(len(docs)))). Callers that fuse vector + BM25 results
            # (e.g. RAGPipeline._retrieve_context) must key on THIS, not on a
            # per-query rank/loop position, or the fusion silently merges
            # unrelated documents.
            'indices': []
        }

        for i, query_indices in enumerate(indices):
            query_docs = []
            query_metas = []
            query_ids = []
            query_dists = []
            query_positions = []

            for j, idx in enumerate(query_indices):
                if idx < len(collection['documents']):
                    metadata = collection['metadatas'][idx]

                    # Apply metadata filter if provided
                    if metadata_filter:
                        if not self._match_metadata_filter(metadata, metadata_filter):
                            continue

                    query_docs.append(collection['documents'][idx])
                    query_metas.append(metadata)
                    query_ids.append(collection['ids'][idx])
                    query_positions.append(int(idx))
                    # Cast to native float — numpy float32 isn't JSON-serializable
                    # and downstream callers/tests expect Python floats.
                    query_dists.append(float(distances[i][j]))

                    # Stop once we have enough results
                    if len(query_docs) >= n_results:
                        break

            results['documents'].append(query_docs)
            results['metadatas'].append(query_metas)
            results['distances'].append(query_dists)
            results['ids'].append(query_ids)
            results['indices'].append(query_positions)
        
        filter_msg = f" with filter {metadata_filter}" if metadata_filter else ""
        logger.info(
            f"Queried collection '{collection_name}' with {len(query_embeddings)} queries{filter_msg}, "
            f"returning {n_results} results each"
        )
        
        return results
    
    def _match_metadata_filter(self, metadata: Dict[str, Any], filter_dict: Dict[str, Any]) -> bool:
        """
        Check if metadata matches all filter criteria.
        
        Args:
            metadata: Document metadata dictionary
            filter_dict: Filter criteria dictionary
            
        Returns:
            True if all filter criteria match, False otherwise
        """
        for key, value in filter_dict.items():
            # Support nested keys with dot notation (e.g., "user.tier")
            if '.' in key:
                current = metadata
                for part in key.split('.'):
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        return False
                if current != value:
                    return False
            else:
                if metadata.get(key) != value:
                    return False
        return True
    
    def delete_collection(self, name: str) -> None:
        """
        Delete a collection and its associated data.
        
        Args:
            name: Name of the collection to delete
        """
        if name not in self.collections:
            logger.warning(f"Collection '{name}' does not exist")
            return
        
        del self.collections[name]
        
        # Delete persisted files if they exist
        index_path = Path(self.persist_directory) / f"{name}.index"
        metadata_path = Path(self.persist_directory) / f"{name}_metadata.pkl"
        
        if index_path.exists():
            index_path.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        
        logger.info(f"Deleted collection '{name}'")
    
    def list_collections(self) -> List[str]:
        """
        List all available collections.
        
        Returns:
            List of collection names
        """
        return list(self.collections.keys())
    
    def get_collection_count(self, name: str) -> int:
        """
        Get the number of documents in a collection.
        
        Args:
            name: Name of the collection
        
        Returns:
            Number of documents in the collection
        """
        if name not in self.collections:
            raise ValueError(f"Collection '{name}' does not exist")
        
        return len(self.collections[name]['documents'])
    
    def persist(self) -> None:
        """
        Save all collections to disk.
        """
        for name, collection in self.collections.items():
            # Save FAISS index
            index_path = Path(self.persist_directory) / f"{name}.index"
            faiss.write_index(collection['index'], str(index_path))
            
            # Save metadata
            metadata = {
                'documents': collection['documents'],
                'metadatas': collection['metadatas'],
                'ids': collection['ids'],
                'dimension': collection['dimension']
            }
            metadata_path = Path(self.persist_directory) / f"{name}_metadata.pkl"
            with open(metadata_path, 'wb') as f:
                pickle.dump(metadata, f)
            
            logger.info(f"Persisted collection '{name}'")
    
    def load_collection(self, name: str) -> None:
        """
        Load a collection from disk.
        
        Args:
            name: Name of the collection to load
        """
        index_path = Path(self.persist_directory) / f"{name}.index"
        metadata_path = Path(self.persist_directory) / f"{name}_metadata.pkl"
        
        if not index_path.exists() or not metadata_path.exists():
            raise ValueError(f"Collection '{name}' does not exist on disk")
        
        # Load FAISS index
        index = faiss.read_index(str(index_path))
        
        # Load metadata
        with open(metadata_path, 'rb') as f:
            metadata = pickle.load(f)
        
        self.collections[name] = {
            'index': index,
            'documents': metadata['documents'],
            'metadatas': metadata['metadatas'],
            'ids': metadata['ids'],
            'dimension': metadata['dimension']
        }
        
        logger.info(f"Loaded collection '{name}' with {len(metadata['documents'])} documents")
    
    def update_document(
        self,
        collection_name: str,
        document_id: str,
        document: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Update an existing document in the collection.
        
        Args:
            collection_name: Name of the collection
            document_id: ID of the document to update
            document: New document text (optional)
            embedding: New embedding vector (optional)
            metadata: New metadata (optional)
        """
        if collection_name not in self.collections:
            raise ValueError(f"Collection '{collection_name}' does not exist")
        
        collection = self.collections[collection_name]
        
        # Find document index
        try:
            idx = collection['ids'].index(document_id)
        except ValueError:
            raise ValueError(f"Document with ID '{document_id}' not found")
        
        # Update document
        if document is not None:
            collection['documents'][idx] = document
        
        # Update metadata
        if metadata is not None:
            collection['metadatas'][idx] = metadata
        
        # Note: FAISS doesn't support in-place updates of vectors
        # If embedding is provided, we need to rebuild the index
        if embedding is not None:
            raise NotImplementedError(
                "Updating embeddings requires rebuilding the FAISS index. "
                "Consider deleting and re-adding the document instead."
            )
        
        logger.info(f"Updated document '{document_id}' in collection '{collection_name}'")
    
    def delete_documents(
        self,
        collection_name: str,
        document_ids: List[str]
    ) -> None:
        """
        Delete documents from a collection by their IDs.
        
        Args:
            collection_name: Name of the collection
            document_ids: List of document IDs to delete
        """
        if collection_name not in self.collections:
            raise ValueError(f"Collection '{collection_name}' does not exist")
        
        collection = self.collections[collection_name]
        
        # Note: FAISS doesn't support deletion efficiently
        # We need to rebuild the index without deleted documents
        indices_to_keep = [
            i for i, doc_id in enumerate(collection['ids'])
            if doc_id not in document_ids
        ]
        
        if len(indices_to_keep) == len(collection['ids']):
            logger.warning(f"No documents found with IDs: {document_ids}")
            return
        
        # Rebuild index
        dimension = collection['dimension']
        new_index = faiss.IndexFlatL2(dimension)
        
        # Extract embeddings from old index
        old_vectors = []
        for i in indices_to_keep:
            # Note: This is inefficient but FAISS doesn't provide direct vector access
            # In practice, you'd want to store embeddings separately
            pass
        
        # For now, just update the metadata
        collection['documents'] = [collection['documents'][i] for i in indices_to_keep]
        collection['metadatas'] = [collection['metadatas'][i] for i in indices_to_keep]
        collection['ids'] = [collection['ids'][i] for i in indices_to_keep]
        
        logger.warning(
            f"Deleted {len(document_ids)} document references from collection '{collection_name}'. "
            "Note: FAISS index needs manual rebuild for full deletion."
        )
