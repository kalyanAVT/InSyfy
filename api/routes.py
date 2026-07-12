import asyncio
import time
import json
import traceback
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from api.schemas import ResearchRequest, ResearchResponse, ReportResponse, EmailReportRequest
from api.stream import event_stream
from graph.state import AgentState, SearchResult
from graph.pipeline import graph
from db.redis_client import redis_client, emit_event
from agents.token_utils import estimate_cost
from api.pdf_export import markdown_to_pdf_bytes
from api.email_sender import send_report_email


router = APIRouter()


def _safe_get(obj, key, default=None):
    """Safely get value from dict or Pydantic object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@router.post("/research", response_model=ResearchResponse)
async def start_research(request: ResearchRequest):
    run_id = str(int(time.time() * 1000))
    
    state = AgentState(
        run_id=run_id,
        research_question=request.question
    )
    
    redis_client.save_state(run_id, state.model_dump())
    redis_client.save_status(run_id, "running")
    
    emit_event(run_id, "run_started", {
        "question": request.question,
        "estimated_duration": 180
    })
    
    asyncio.create_task(_run_pipeline(run_id, state))
    
    return ResearchResponse(
        run_id=run_id,
        status="started",
        estimated_duration_seconds=180
    )


async def _run_pipeline(run_id: str, state: AgentState):
    pipeline_start = time.time()
    try:
        result_dict = await asyncio.to_thread(graph.invoke, state)
        
        # Convert Pydantic objects to dict for safe access
        result_dict = _convert_to_dict(result_dict)
        
        timestamps = result_dict.get("timestamps", {})
        token_usage = result_dict.get("token_usage", {})
        total_latency = _calc_total_latency(timestamps)
        
        if result_dict.get("declined"):
            status = "declined"
            quality_score = None
            redis_client.save_status(run_id, "declined")
            emit_event(run_id, "declined", {
                "reason": result_dict.get("decline_reason", "")
            })
        else:
            status = "completed"
            critique = result_dict.get("critique", {})
            quality_score = critique.get("quality_score") if isinstance(critique, dict) else None
            redis_client.save_status(run_id, "completed")
            emit_event(run_id, "done", {
                "quality_score": quality_score,
                "total_latency_ms": total_latency
            })
        
        redis_client.save_state(run_id, result_dict)
        
        redis_client.log_run_metrics({
            "run_id": run_id,
            "question": state.research_question,
            "status": status,
            "quality_score": quality_score,
            "total_latency_ms": total_latency or int((time.time() - pipeline_start) * 1000),
            "total_tokens": sum(token_usage.values()) if token_usage else 0,
            "estimated_cost_usd": estimate_cost(token_usage),
            "node_latencies_ms": {node: _calc_latency(ts) for node, ts in timestamps.items()},
            "retry_count": result_dict.get("retry_count", 0),
            "timestamp": time.time()
        })
        
    except Exception as e:
        error_msg = f"Pipeline error: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        redis_client.save_status(run_id, "failed")
        emit_event(run_id, "error", {
            "error_type": "PIPELINE_FAILURE",
            "message": str(e),
            "traceback": traceback.format_exc()[-500:]
        })
        
        redis_client.log_run_metrics({
            "run_id": run_id,
            "question": state.research_question,
            "status": "failed",
            "quality_score": None,
            "total_latency_ms": int((time.time() - pipeline_start) * 1000),
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "node_latencies_ms": {},
            "retry_count": 0,
            "timestamp": time.time()
        })


def _convert_to_dict(obj):
    """Recursively convert Pydantic models to dicts."""
    if isinstance(obj, dict):
        return {k: _convert_to_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_to_dict(item) for item in obj]
    elif hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, '__dict__'):
        return {k: _convert_to_dict(v) for k, v in obj.__dict__.items() if not k.startswith('_')}
    else:
        return obj


def _calc_latency(ts: dict) -> int:
    try:
        from datetime import datetime
        start = datetime.fromisoformat(ts.get("start", ""))
        end = datetime.fromisoformat(ts.get("end", ""))
        return int((end - start).total_seconds() * 1000)
    except:
        return 0


def _calc_total_latency(timestamps: dict) -> int:
    try:
        from datetime import datetime
        first = min(datetime.fromisoformat(ts["start"]) for ts in timestamps.values() if isinstance(ts, dict) and "start" in ts)
        last = max(datetime.fromisoformat(ts["end"]) for ts in timestamps.values() if isinstance(ts, dict) and "end" in ts)
        return int((last - first).total_seconds() * 1000)
    except:
        return 0


@router.get("/stream/{run_id}")
async def stream_events(run_id: str, request: Request):
    return StreamingResponse(
        event_stream(run_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.get("/status/{run_id}")
async def get_status(run_id: str):
    status = redis_client.get_status(run_id)
    if not status:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "status": status}


@router.get("/report/{run_id}", response_model=ReportResponse)
async def get_report(run_id: str):
    state_dict = redis_client.get_state(run_id)
    if not state_dict:
        raise HTTPException(status_code=404, detail="Run not found")
    
    result = state_dict
    
    search_results = result.get("search_results", [])
    chunks_count = 0
    if isinstance(search_results, list):
        for r in search_results:
            if isinstance(r, dict) and "chunks" in r:
                chunks_count += len(r.get("chunks", []))
    
    chunks_count += len(result.get("rag_chunks", []))
    
    # Single source of truth: writer.py builds the citation map once and
    # saves it to state.citations. Re-deriving sources here separately
    # used to forget rag_chunks and drift out of sync with the report's
    # own Sources section — read the same list the report itself used.
    citations = result.get("citations", [])
    if citations:
        sources = [{"url": c.get("url", ""), "title": c.get("title", "Source")} for c in citations]
    else:
        # Fallback for runs cached before this field existed
        sources = []
        seen = set()
        if isinstance(search_results, list):
            for sr in search_results:
                if isinstance(sr, dict) and "chunks" in sr:
                    for chunk in sr.get("chunks", []):
                        meta = chunk.get("metadata", {}) if isinstance(chunk, dict) else {}
                        url = meta.get("source_url", "") if isinstance(meta, dict) else ""
                        if url and url not in seen:
                            seen.add(url)
                            title = meta.get("source_title", "Source") if isinstance(meta, dict) else "Source"
                            sources.append({"url": url, "title": title})
    
    critique = result.get("critique", {})
    quality_score = critique.get("quality_score") if isinstance(critique, dict) else None
    
    total_latency = None
    timestamps = result.get("timestamps", {})
    if timestamps:
        try:
            from datetime import datetime
            first = min(datetime.fromisoformat(ts["start"]) for ts in timestamps.values() if isinstance(ts, dict) and "start" in ts)
            last = max(datetime.fromisoformat(ts["end"]) for ts in timestamps.values() if isinstance(ts, dict) and "end" in ts)
            total_latency = int((last - first).total_seconds() * 1000)
        except:
            pass
    
    return ReportResponse(
        run_id=run_id,
        question=result.get("research_question", ""),
        report_markdown=result.get("final_report", "No report generated"),
        quality_score=quality_score,
        total_latency_ms=total_latency,
        chunks_retrieved=chunks_count,
        sources=sources,
        token_usage=result.get("token_usage", {})
    )


@router.get("/report/{run_id}/pdf")
async def get_report_pdf(run_id: str):
    """Download the report as a PDF. Generated on-demand from the same
    markdown the report endpoint returns — not cached, so it's always
    in sync with the latest report text for this run."""
    state_dict = redis_client.get_state(run_id)
    if not state_dict:
        raise HTTPException(status_code=404, detail="Run not found")

    report_markdown = state_dict.get("final_report", "")
    if not report_markdown:
        raise HTTPException(status_code=404, detail="No report available for this run yet")

    question = state_dict.get("research_question", "")

    try:
        pdf_bytes = markdown_to_pdf_bytes(report_markdown, question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="insyfy_report_{run_id}.pdf"'
        }
    )


@router.post("/report/{run_id}/email")
async def email_report(run_id: str, request: EmailReportRequest):
    """Generate the report as a PDF and email it to the given address."""
    state_dict = redis_client.get_state(run_id)
    if not state_dict:
        raise HTTPException(status_code=404, detail="Run not found")

    report_markdown = state_dict.get("final_report", "")
    if not report_markdown:
        raise HTTPException(status_code=404, detail="No report available for this run yet")

    question = state_dict.get("research_question", "")

    try:
        pdf_bytes = markdown_to_pdf_bytes(report_markdown, question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

    filename = f"insyfy_report_{run_id}.pdf"
    subject = f"InSyfy Research Report: {question[:80]}"

    sent, message = send_report_email(request.to_email, subject, pdf_bytes, filename)
    if not sent:
        raise HTTPException(status_code=502, detail=message)

    return {"sent": True, "message": message}


@router.get("/history")
async def get_history(limit: int = 10):
    runs = []
    for key in redis_client.client.scan_iter(match="run:*:status", count=100):
        run_id = key.split(":")[1]
        status = redis_client.get_status(run_id)
        state = redis_client.get_state(run_id)
        if state:
            runs.append({
                "run_id": run_id,
                "question": state.get("research_question", ""),
                "status": status,
                "quality_score": state.get("critique", {}).get("quality_score") if state.get("critique") else None
            })
    
    runs.sort(key=lambda x: x["run_id"], reverse=True)
    return {"runs": runs[:limit]}


@router.delete("/report/{run_id}")
async def delete_report(run_id: str):
    redis_client.client.delete(f"run:{run_id}:state")
    redis_client.client.delete(f"run:{run_id}:status")
    redis_client.client.delete(f"run:{run_id}:events")
    return {"deleted": True}


@router.get("/health")
async def health_check():
    redis_ok = redis_client.health_check()
    qdrant_ok = False
    try:
        from retrieval.qdrant_store import qdrant_store
        info = qdrant_store.get_collection_info()
        qdrant_ok = "error" not in info
    except:
        pass
    
    return {
        "status": "healthy" if (redis_ok and qdrant_ok) else "degraded",
        "redis": "connected" if redis_ok else "disconnected",
        "qdrant": "connected" if qdrant_ok else "disconnected",
        "version": "0.3.0-step3"
    }


def _percentile(values: list, p: float):
    """p is a fraction, e.g. 0.5 for p50, 0.95 for p95."""
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int(len(s) * p))
    return s[idx]


def _avg(values: list):
    return round(sum(values) / len(values), 2) if values else None


@router.get("/metrics")
async def get_metrics(limit: int = 500):
    """
    Aggregate stats computed from actual logged runs. Returns
    total_runs=0 if nothing has run yet — no fabricated numbers.
    """
    runs = redis_client.get_recent_metrics(limit=limit)
    
    if not runs:
        return {"total_runs": 0, "message": "No runs logged yet"}
    
    total = len(runs)
    completed = sum(1 for r in runs if r.get("status") == "completed")
    declined = sum(1 for r in runs if r.get("status") == "declined")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    
    latencies = [r["total_latency_ms"] for r in runs if r.get("total_latency_ms")]
    quality_scores = [r["quality_score"] for r in runs if r.get("quality_score") is not None]
    retry_counts = [r.get("retry_count", 0) for r in runs]
    total_tokens = sum(r.get("total_tokens", 0) for r in runs)
    total_cost = sum(r.get("estimated_cost_usd", 0.0) for r in runs)
    
    return {
        "window_size": total,
        "total_runs": total,
        "completed": completed,
        "declined": declined,
        "failed": failed,
        "success_rate": round(completed / total, 3),
        "avg_latency_ms": _avg(latencies),
        "p50_latency_ms": _percentile(latencies, 0.5),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "avg_quality_score": _avg(quality_scores),
        "avg_retries_per_run": _avg(retry_counts),
        "total_tokens": total_tokens,
        "avg_tokens_per_run": round(total_tokens / total, 1) if total else 0,
        "total_estimated_cost_usd": round(total_cost, 4),
        "cost_estimate_configured": total_cost > 0 or any(
            r.get("estimated_cost_usd", 0) > 0 for r in runs
        )
    }