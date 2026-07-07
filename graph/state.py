from typing import List, Optional, Dict, Any, Annotated
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4


def merge_lists(existing: List, new: List) -> List:
    """Generic reducer: merge two lists."""
    return existing + new


def merge_dicts(existing: Dict, new: Dict) -> Dict:
    """Reducer: merge two dictionaries (for timestamps)."""
    merged = dict(existing)
    merged.update(new)
    return merged


def merge_token_usage(existing: Dict[str, int], new: Dict[str, int]) -> Dict[str, int]:
    """Reducer: sum token counts per node instead of overwriting.

    Unlike timestamps, a node's token cost from a retry should ADD to its
    running total, not replace the previous attempt's count — both calls
    actually cost tokens.
    """
    merged = dict(existing)
    for key, value in new.items():
        merged[key] = merged.get(key, 0) + value
    return merged


class SubQuery(BaseModel):
    query: str = Field(description="The search query text")
    intent: str = Field(description="What this query is trying to find")
    priority: int = Field(default=1, description="Priority order (1 = highest)")


class ResearchPlan(BaseModel):
    sub_queries: List[SubQuery] = Field(description="List of focused search queries")
    output_format: str = Field(default="report", description="Expected report structure")
    quality_threshold: float = Field(default=0.75, description="Minimum acceptable quality score")
    domain_hints: List[str] = Field(default_factory=list, description="Inferred domains")
    gap_analysis: str = Field(default="", description="Notes from Critic on retry runs")


class ChunkMetadata(BaseModel):
    source_url: str
    source_title: str = ""
    page_num: int = 0
    paragraph_idx: int = 0
    char_start: int = 0
    char_end: int = 0
    retrieval_timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Chunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: str(uuid4()))
    text: str
    metadata: ChunkMetadata
    score: float = 0.0
    retrieval_method: str = "web_search"


class SearchResult(BaseModel):
    sub_query: str
    sources: List[str] = Field(default_factory=list)
    chunks: List[Chunk] = Field(default_factory=list)


class Finding(BaseModel):
    claim: str
    sub_query: str = Field(
        default="",
        description="The exact text of the sub-query this finding primarily answers, if identifiable"
    )
    supporting_chunks: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    contradictions: List[str] = Field(default_factory=list)


class SynthesisResult(BaseModel):
    executive_summary: str = Field(
        default="",
        description="A coherent 3-5 sentence prose summary of the overall answer, "
                     "not a concatenation of individual claims"
    )
    key_findings: List[Finding] = Field(default_factory=list)
    overall_confidence: float = 0.0
    source_diversity_score: float = 0.0
    coverage_assessment: Dict[str, bool] = Field(default_factory=dict)


class CritiqueResult(BaseModel):
    quality_score: float = Field(default=0.0, description="Composite quality score 0-1")
    coverage_score: float = 0.0
    contradiction_score: float = 0.0
    source_diversity_score: float = 0.0
    confidence_score: float = 0.0
    gap_analysis: str = ""
    proceed: bool = False


class AgentError(BaseModel):
    error_type: str
    node: str
    message: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    retry_scheduled: bool = False


class AgentState(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    research_question: str = ""
    plan: Optional[ResearchPlan] = None
    search_results: Annotated[List[SearchResult], merge_lists] = Field(default_factory=list)
    rag_chunks: Annotated[List[Chunk], merge_lists] = Field(default_factory=list)
    synthesis: Optional[SynthesisResult] = None
    critique: Optional[CritiqueResult] = None
    final_report: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    declined: bool = False
    decline_reason: str = ""
    retry_count: int = 0
    quality_warning: bool = False
    timestamps: Annotated[Dict[str, Dict[str, str]], merge_dicts] = Field(default_factory=dict)
    token_usage: Annotated[Dict[str, int], merge_token_usage] = Field(default_factory=dict)
    errors: Annotated[List[AgentError], merge_lists] = Field(default_factory=list)
    
    class Config:
        arbitrary_types_allowed = True