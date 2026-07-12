from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict


class ResearchRequest(BaseModel):
    question: str = Field(..., min_length=10, max_length=500, description="The research question")
    depth: Literal["quick", "standard", "deep"] = "standard"
    output_format: Literal["report", "bullets", "summary"] = "report"


class ResearchResponse(BaseModel):
    run_id: str
    status: str = "started"
    estimated_duration_seconds: int = 120


class ReportResponse(BaseModel):
    run_id: str
    question: str
    report_markdown: str
    quality_score: Optional[float] = None
    total_latency_ms: Optional[int] = None
    chunks_retrieved: int = 0
    sources: list = []
    token_usage: Dict[str, int] = {}


class EmailReportRequest(BaseModel):
    to_email: str = Field(..., description="Recipient email address")