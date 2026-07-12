import os
import numpy as np
from typing import List
from dotenv import load_dotenv

load_dotenv()

from sentence_transformers import SentenceTransformer
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from graph.state import SynthesisResult, Finding, Chunk
from agents.token_utils import extract_token_count
from prompts.loader import load_prompt


class SynthesizerAgent:
    """Merges search chunks into structured findings with confidence scores."""
    
    def __init__(self):
        provider = os.getenv("LLM_PROVIDER", "groq")
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        
        if provider == "groq":
            self.llm = ChatGroq(
                model=model,
                temperature=0.2,
                api_key=os.getenv("GROQ_API_KEY")
            )
        else:
            self.llm = ChatOpenAI(
                model=model,
                temperature=0.2,
                api_key=os.getenv("OPENAI_API_KEY")
            )
        
        self.parser = PydanticOutputParser(pydantic_object=SynthesisResult)
        self.embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        self.last_token_usage = 0
    
    def _deduplicate_chunks(self, chunks: List[Chunk], threshold: float = 0.92) -> List[Chunk]:
        """Remove near-duplicate chunks based on cosine similarity."""
        if not chunks:
            return []
        
        texts = [c.text for c in chunks]
        embeddings = self.embedder.encode(texts)
        
        unique_chunks = [chunks[0]]
        unique_embeddings = [embeddings[0]]
        
        for i in range(1, len(chunks)):
            similarities = [
                np.dot(embeddings[i], emb) / (np.linalg.norm(embeddings[i]) * np.linalg.norm(emb))
                for emb in unique_embeddings
            ]
            
            if max(similarities) < threshold:
                unique_chunks.append(chunks[i])
                unique_embeddings.append(embeddings[i])
        
        return unique_chunks
    
    def run(self, research_question: str, chunks: List[Chunk], sub_queries: List[str] = None) -> SynthesisResult:
        sub_queries = sub_queries or []
        if not chunks:
            return SynthesisResult(
                executive_summary="No content was retrieved for this question, so no findings could be produced.",
                key_findings=[],
                overall_confidence=0.0,
                source_diversity_score=0.0
            )
        
        chunks = self._deduplicate_chunks(chunks)
        
        # LIMIT: max 15 chunks for context to avoid token overflow
        context_parts = []
        for chunk in chunks[:15]:
            context_parts.append(
                f"[CHUNK_ID: {chunk.chunk_id}]\nSource: {chunk.metadata.source_url}\n{chunk.text[:500]}...\n"
            )
        
        context = "\n".join(context_parts)
        sub_queries_text = "\n".join(f"- {sq}" for sq in sub_queries) if sub_queries else "(none provided)"
        
        prompt_data = load_prompt("synthesizer")
        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt_data["system_template"]),
            ("human", prompt_data["user_template"])
        ])
        
        self.last_token_usage = 0
        
        try:
            messages = prompt.format_messages(
                question=research_question,
                sub_queries=sub_queries_text,
                context=context,
                format_instructions=self.parser.get_format_instructions()
            )
            ai_message = self.llm.invoke(messages)
            self.last_token_usage = extract_token_count(ai_message)
            
            result = self.parser.parse(ai_message.content)
            return result
        except Exception as e:
            return self._fallback_synthesis(chunks)
    
    def _fallback_synthesis(self, chunks: List[Chunk]) -> SynthesisResult:
        """Create basic findings if LLM parsing fails."""
        findings = []
        for chunk in chunks[:5]:
            findings.append(Finding(
                claim=chunk.text[:200] + "...",
                supporting_chunks=[chunk.chunk_id],
                confidence=0.5
            ))
        
        return SynthesisResult(
            executive_summary=(
                "Automated synthesis was unavailable for this run, so this summary "
                "falls back to raw excerpts from the highest-scoring retrieved sources "
                "rather than a generated synthesis. See Key Findings below for details."
            ),
            key_findings=findings,
            overall_confidence=0.5,
            source_diversity_score=0.5
        )