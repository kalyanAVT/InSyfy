import time
from datetime import datetime
from typing import Any
from graph.state import AgentState, AgentError, CritiqueResult
from agents.planner import PlannerAgent
from agents.searcher import SearchAgent
from agents.memory_rag import MemoryRAGAgent
from agents.synthesizer import SynthesizerAgent
from agents.critic import CriticAgent
from agents.writer import WriterAgent
from retrieval.citation_enforcement import CitationEnforcement
from db.redis_client import emit_event


def planner_node(state: AgentState) -> dict[str, Any]:
    """Execute the Planner Agent."""
    start_time = time.time()
    emit_event(state.run_id, "node_start", {"node": "planner"})

    result = {}

    # If this is a retry, clear old search results to prevent accumulation
    if state.retry_count > 0:
        print(f"PLANNER: Retry {state.retry_count}/2 — clearing old search results")
        result["search_results"] = []  # Will be merged (replaced effectively since we send new list)

    try:
        agent = PlannerAgent()
        gap = state.critique.gap_analysis if state.critique else ""
        plan = agent.run(state.research_question, gap_analysis=gap)

        result.update({
            "plan": plan,
            "token_usage": {"planner": agent.last_token_usage},
            "timestamps": {
                **state.timestamps,
                "planner": {
                    "start": datetime.utcfromtimestamp(start_time).isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        })
        emit_event(state.run_id, "node_complete", {
            "node": "planner",
            "latency_ms": int((time.time() - start_time) * 1000),
            "tokens": agent.last_token_usage
        })
        return result

    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "planner", "message": str(e)})
        return {
            "plan": None,
            "errors": [
                *state.errors,
                AgentError(
                    error_type="VALIDATION_ERROR",
                    node="planner",
                    message=str(e)
                )
            ]
        }


def search_node(state: AgentState) -> dict[str, Any]:
    """
    Execute a single Search Agent.
    Called via Send() with sub-query passed as research_question.
    """
    start_time = time.time()

    query = state.research_question
    emit_event(state.run_id, "node_start", {"node": "searcher", "query": query})

    try:
        agent = SearchAgent()
        result = agent.run(query)

        emit_event(state.run_id, "node_complete", {
            "node": "searcher",
            "query": query,
            "latency_ms": int((time.time() - start_time) * 1000)
        })
        return {
            "search_results": [result],
            "timestamps": {
                **state.timestamps,
                f"searcher_{query[:20]}": {
                    "start": datetime.utcfromtimestamp(start_time).isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        }
    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "searcher", "query": query, "message": str(e)})
        return {
            "errors": [
                *state.errors,
                AgentError(
                    error_type="TOOL_FAILURE",
                    node="searcher",
                    message=str(e)
                )
            ]
        }


def memory_rag_node(state: AgentState) -> dict[str, Any]:
    """Execute Memory RAG Agent."""
    start_time = time.time()
    emit_event(state.run_id, "node_start", {"node": "memory_rag"})

    sub_queries = [sq.query for sq in state.plan.sub_queries] if state.plan else []

    try:
        agent = MemoryRAGAgent()
        chunks = agent.run(
            query=state.research_question,
            sub_queries=sub_queries
        )

        emit_event(state.run_id, "node_complete", {
            "node": "memory_rag",
            "chunks_retrieved": len(chunks),
            "latency_ms": int((time.time() - start_time) * 1000)
        })
        return {
            "rag_chunks": chunks,
            "timestamps": {
                **state.timestamps,
                "memory_rag": {
                    "start": datetime.utcfromtimestamp(start_time).isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        }
    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "memory_rag", "message": str(e)})
        return {
            "errors": [
                *state.errors,
                AgentError(
                    error_type="RETRIEVAL_FAILURE",
                    node="memory_rag",
                    message=str(e)
                )
            ]
        }


def merge_search_results(state: AgentState) -> dict[str, Any]:
    """Merge parallel search results. No-op since reducer handles merging."""
    return {}


def synthesizer_node(state: AgentState) -> dict[str, Any]:
    """Execute the Synthesizer Agent."""
    start_time = time.time()
    emit_event(state.run_id, "node_start", {"node": "synthesizer"})

    all_chunks = []
    for result in state.search_results:
        all_chunks.extend(result.chunks)
    all_chunks.extend(state.rag_chunks)

    try:
        agent = SynthesizerAgent()
        synthesis = agent.run(
            research_question=state.research_question,
            chunks=all_chunks,
            sub_queries=[sq.query for sq in state.plan.sub_queries] if state.plan else []
        )

        emit_event(state.run_id, "node_complete", {
            "node": "synthesizer",
            "findings_count": len(synthesis.key_findings) if synthesis else 0,
            "latency_ms": int((time.time() - start_time) * 1000),
            "tokens": agent.last_token_usage
        })
        return {
            "synthesis": synthesis,
            "token_usage": {"synthesizer": agent.last_token_usage},
            "timestamps": {
                **state.timestamps,
                "synthesizer": {
                    "start": datetime.utcfromtimestamp(start_time).isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        }
    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "synthesizer", "message": str(e)})
        return {
            "errors": [
                *state.errors,
                AgentError(
                    error_type="UNEXPECTED",
                    node="synthesizer",
                    message=str(e)
                )
            ]
        }


def citation_enforcement_node(state: AgentState) -> dict[str, Any]:
    """Check that all claims are supported by evidence."""
    emit_event(state.run_id, "node_start", {"node": "citation_enforcement"})

    if not state.synthesis or not state.synthesis.key_findings:
        emit_event(state.run_id, "node_complete", {
            "node": "citation_enforcement",
            "passed": False,
            "reason": "No findings produced by synthesizer"
        })
        return {
            "declined": True,
            "decline_reason": "No findings produced by synthesizer"
        }

    all_chunks = []
    for result in state.search_results:
        all_chunks.extend(result.chunks)
    all_chunks.extend(state.rag_chunks)

    enforcer = CitationEnforcement()
    passed, reason = enforcer.check(state.synthesis.key_findings, all_chunks)

    emit_event(state.run_id, "node_complete", {
        "node": "citation_enforcement",
        "passed": passed,
        "reason": reason
    })

    if not passed:
        return {
            "declined": True,
            "decline_reason": reason
        }

    return {}


def critic_node(state: AgentState) -> dict[str, Any]:
    """Execute the Critic Agent."""
    start_time = time.time()
    emit_event(state.run_id, "node_start", {"node": "critic"})

    if not state.synthesis or not state.plan:
        emit_event(state.run_id, "node_error", {"node": "critic", "message": "Missing synthesis or plan"})
        return {
            "critique": None,
            "errors": [
                *state.errors,
                AgentError(
                    error_type="VALIDATION_ERROR",
                    node="critic",
                    message="Missing synthesis or plan"
                )
            ]
        }

    try:
        agent = CriticAgent()
        critique = agent.run(
            synthesis=state.synthesis,
            plan=state.plan,
            retry_count=state.retry_count
        )

        # Increment retry count if not proceeding and under limit
        result = {
            "critique": critique,
            "token_usage": {"critic": agent.last_token_usage},
            "timestamps": {
                **state.timestamps,
                "critic": {
                    "start": datetime.utcfromtimestamp(start_time).isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        }

        threshold = state.plan.quality_threshold if state.plan else 0.75

        # If critic says don't proceed and we haven't maxed retries, increment
        if not critique.proceed and state.retry_count < 2:
            result["retry_count"] = state.retry_count + 1
            print(f"CRITIC: Retry {result['retry_count']}/2 triggered")

        emit_event(state.run_id, "node_complete", {
            "node": "critic",
            "quality_score": critique.quality_score,
            "proceed": critique.proceed,
            "latency_ms": int((time.time() - start_time) * 1000),
            "tokens": agent.last_token_usage
        })
        return result

    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "critic", "message": str(e)})
        return {
            "critique": CritiqueResult(
                quality_score=0.6,
                proceed=True,
                gap_analysis=f"Critic failed: {str(e)}. Proceeding with caution."
            ),
            "errors": [
                *state.errors,
                AgentError(
                    error_type="UNEXPECTED",
                    node="critic",
                    message=str(e)
                )
            ]
        }


def writer_node(state: AgentState) -> dict[str, Any]:
    """Execute the Writer Agent."""
    start_time = time.time()
    emit_event(state.run_id, "node_start", {"node": "writer"})
    try:
        agent = WriterAgent()
        report = agent.run(state)

        emit_event(state.run_id, "node_complete", {
            "node": "writer",
            "latency_ms": int((time.time() - start_time) * 1000)
        })
        return {
            "final_report": report,
            "citations": agent.last_citations,
            "timestamps": {
                **state.timestamps,
                "writer": {
                    "start": datetime.utcnow().isoformat(),
                    "end": datetime.utcnow().isoformat()
                }
            }
        }
    except Exception as e:
        emit_event(state.run_id, "node_error", {"node": "writer", "message": str(e)})
        return {
            "final_report": f"# Error\nReport generation failed: {str(e)}",
            "errors": [
                *state.errors,
                AgentError(
                    error_type="UNEXPECTED",
                    node="writer",
                    message=str(e)
                )
            ]
        }


def graceful_degrade_node(state: AgentState) -> dict[str, Any]:
    """Writer with quality warning after max retries."""
    result = writer_node(state)
    result["quality_warning"] = True
    return result