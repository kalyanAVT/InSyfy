import os
from typing import List
from uuid import uuid4
from dotenv import load_dotenv

load_dotenv()

from sentence_transformers import SentenceTransformer, CrossEncoder
from retrieval.qdrant_store import qdrant_store
from graph.state import Chunk, ChunkMetadata


class MemoryRAGAgent:
    """
    Retrieves relevant chunks from Qdrant using hybrid retrieval:
    1. Vector search (semantic similarity)
    2. Simple keyword overlap scoring
    3. Cross-encoder re-ranking on top results
    """
    
    def __init__(self):
        self.embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self.reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    
    def run(self, query: str, sub_queries: List[str] = None) -> List[Chunk]:
        """
        Retrieve chunks using hybrid approach.
        Returns top 8 chunks after re-ranking.
        """
        # 1. Vector search via Qdrant
        query_embedding = self.embedder.encode(query).tolist()
        vector_results = qdrant_store.search(query_embedding, limit=20)
        
        if not vector_results:
            return []
        
        # 2. Simple keyword scoring
        keywords = set(query.lower().split())
        if sub_queries:
            for sq in sub_queries:
                keywords.update(sq.lower().split())
        
        # Remove common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                     "being", "have", "has", "had", "do", "does", "did", "will",
                     "would", "could", "should", "may", "might", "must", "shall",
                     "can", "need", "dare", "ought", "used", "to", "of", "in",
                     "for", "on", "with", "at", "by", "from", "as", "into",
                     "through", "during", "before", "after", "above", "below",
                     "between", "under", "and", "but", "or", "yet", "so",
                     "if", "because", "although", "though", "while", "where",
                     "when", "that", "which", "who", "whom", "whose", "what",
                     "whatever", "whoever", "whomever", "whichever", "this",
                     "these", "those", "such", "it", "its"}
        keywords = {k for k in keywords if k not in stop_words and len(k) > 2}
        
        scored_results = []
        for result in vector_results:
            payload = result.get("payload", {})
            text = payload.get("text", "").lower()
            
            keyword_hits = sum(1 for kw in keywords if kw in text)
            keyword_score = keyword_hits / max(len(keywords), 1)
            
            combined_score = (0.7 * result.get("score", 0)) + (0.3 * keyword_score)
            
            scored_results.append({
                "result": result,
                "combined_score": combined_score,
                "text": payload.get("text", ""),
                "metadata": payload
            })
        
        # Sort by combined score, take top 15 for re-ranking
        scored_results.sort(key=lambda x: x["combined_score"], reverse=True)
        top_for_rerank = scored_results[:15]
        
        # 3. Cross-encoder re-ranking
        pairs = [(query, item["text"]) for item in top_for_rerank]
        rerank_scores = self.reranker.predict(pairs)
        
        for item, score in zip(top_for_rerank, rerank_scores):
            item["rerank_score"] = float(score)
        
        # Sort by re-rank score, return top 8
        top_for_rerank.sort(key=lambda x: x["rerank_score"], reverse=True)
        top_final = top_for_rerank[:8]
        
        chunks = []
        for item in top_final:
            meta = item["metadata"]
            result_id = item["result"].get("id")
            if not result_id:
                result_id = str(uuid4())
                print(f"MEMORY_RAG: Qdrant result missing 'id', generated fallback {result_id}")
            chunks.append(Chunk(
                chunk_id=result_id,
                text=item["text"],
                metadata=ChunkMetadata(
                    source_url=meta.get("source_url", ""),
                    source_title=meta.get("source_title", ""),
                    page_num=meta.get("page_num", 0),
                    paragraph_idx=meta.get("paragraph_idx", 0)
                ),
                score=item["rerank_score"],
                retrieval_method="hybrid"
            ))
        
        return chunks