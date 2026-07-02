import asyncio
import time
import json
import traceback
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from api.schemas import ResearchRequest, ResearchResponse, ReportResponse
from api.stream import event_stream
from graph.state import AgentState, SearchResult
from graph.pipeline import graph
from db.redis_client import redis_client, emit_event


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
    try:
        result_dict = await asyncio.to_thread(graph.invoke, state)
        
        # Convert Pydantic objects to dict for safe access
        result_dict = _convert_to_dict(result_dict)
        
        timestamps = result_dict.get("timestamps", {})
        
        if result_dict.get("declined"):
            redis_client.save_status(run_id, "declined")
            emit_event(run_id, "declined", {
                "reason": result_dict.get("decline_reason", "")
            })
        else:
            redis_client.save_status(run_id, "completed")
            critique = result_dict.get("critique", {})
            quality_score = critique.get("quality_score") if isinstance(critique, dict) else None
            emit_event(run_id, "done", {
                "quality_score": quality_score,
                "total_latency_ms": _calc_total_latency(timestamps)
            })
        
        redis_client.save_state(run_id, result_dict)
        
    except Exception as e:
        error_msg = f"Pipeline error: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        redis_client.save_status(run_id, "failed")
        emit_event(run_id, "error", {
            "error_type": "PIPELINE_FAILURE",
            "message": str(e),
            "traceback": traceback.format_exc()[-500:]
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
        sources=sources
    )


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