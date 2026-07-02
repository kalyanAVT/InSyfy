import os
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


class QdrantStore:
    """
    Qdrant Cloud vector store wrapper.
    Connects to Qdrant free tier (no Docker needed).

    Degrades gracefully if QDRANT_URL/QDRANT_API_KEY are missing: `available`
    is False, self.client is None, and all methods become safe no-ops
    instead of raising. This matters because this class is instantiated as
    a module-level singleton — raising in __init__ would crash the entire
    app at import time, before /health ever got a chance to report
    "degraded" instead of just failing to start.
    """
    
    def __init__(self):
        self.url = os.getenv("QDRANT_URL")
        self.api_key = os.getenv("QDRANT_API_KEY")
        self.client: Optional[QdrantClient] = None
        self.available = False
        
        # Use a new collection name to avoid dimension conflicts
        self.collection_name = "insyfy_research_chunks"
        self.vector_size = 384  # all-MiniLM-L6-v2 dimensions
        
        if not self.url or not self.api_key:
            print(
                "QdrantStore: QDRANT_URL and/or QDRANT_API_KEY not set — "
                "memory/RAG features are disabled for this run.\n"
                f"QDRANT_URL: {'set' if self.url else 'MISSING'}\n"
                f"QDRANT_API_KEY: {'set' if self.api_key else 'MISSING'}"
            )
            return
        
        try:
            self.client = QdrantClient(
                url=self.url,
                api_key=self.api_key,
                timeout=30
            )
            self.available = True
            # Auto-create collection on init
            self.ensure_collection()
        except Exception as e:
            print(f"QdrantStore: failed to connect ({e}) — memory/RAG features are disabled for this run.")
            self.client = None
            self.available = False
    
    def ensure_collection(self):
        """Create collection if it doesn't exist."""
        if not self.available:
            return
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.collection_name not in collection_names:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                print(f"Created collection: {self.collection_name} (dim={self.vector_size})")
            else:
                print(f"Collection {self.collection_name} already exists")
                
        except Exception as e:
            print(f"Error ensuring collection: {e}")
    
    def store_chunks(self, chunks: List, embeddings: List[List[float]], run_id: str):
        """Store chunks with embeddings. No-op if Qdrant is unavailable."""
        if not self.available:
            print("QdrantStore: skipping store_chunks — Qdrant unavailable")
            return
        self.ensure_collection()
        
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            points.append(PointStruct(
                id=chunk.chunk_id,
                vector=embedding,
                payload={
                    "text": chunk.text,
                    "source_url": chunk.metadata.source_url,
                    "source_title": chunk.metadata.source_title,
                    "page_num": chunk.metadata.page_num,
                    "paragraph_idx": chunk.metadata.paragraph_idx,
                    "run_id": run_id,
                    "retrieval_count": 0
                }
            ))
        
        if points:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
    
    def search(self, query_embedding: List[float], limit: int = 8) -> List[dict]:
        """
        Search for similar chunks. Tries multiple API versions.
        Returns [] if Qdrant is unavailable.
        """
        if not self.available:
            return []
        self.ensure_collection()
        
        # Try modern API first (qdrant-client >= 1.7)
        try:
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=limit,
                with_payload=True,
                with_vectors=False
            )
            
            return [
                {
                    "id": point.id,
                    "score": point.score,
                    "payload": point.payload or {},
                }
                for point in results
            ]
            
        except AttributeError as e:
            if "search" in str(e):
                print("Falling back to query_points API...")
                try:
                    # Try query_points (newer API)
                    results = self.client.query_points(
                        collection_name=self.collection_name,
                        query=query_embedding,
                        limit=limit,
                        with_payload=True
                    )
                    
                    return [
                        {
                            "id": point.id,
                            "score": point.score,
                            "payload": point.payload or {},
                        }
                        for point in results.points
                    ]
                    
                except Exception as e2:
                    print(f"query_points also failed: {e2}")
                    return []
            else:
                raise
        
        except Exception as e:
            print(f"Qdrant search error: {e}")
            return []
    
    def get_collection_info(self) -> dict:
        """Get collection info. Returns empty/unavailable if collection doesn't exist or Qdrant isn't configured."""
        if not self.available:
            return {
                "name": self.collection_name,
                "exists": False,
                "points_count": 0,
                "status": "unavailable",
                "error": "QDRANT_URL/QDRANT_API_KEY not set or connection failed"
            }
        try:
            # First check if collection exists
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.collection_name not in collection_names:
                return {
                    "name": self.collection_name,
                    "exists": False,
                    "points_count": 0,
                    "status": "not_created"
                }
            
            info = self.client.get_collection(self.collection_name)
            return {
                "name": self.collection_name,
                "exists": True,
                "vector_size": info.config.params.vectors.size,
                "points_count": info.points_count,
                "status": str(info.status)
            }
        except Exception as e:
            return {"error": str(e), "exists": False}


# Singleton
qdrant_store = QdrantStore()