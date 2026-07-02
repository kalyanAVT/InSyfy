import os
from dotenv import load_dotenv

load_dotenv()

from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from graph.state import AgentState
from graph.nodes import (
    planner_node,
    search_node,
    memory_rag_node,
    merge_search_results,
    synthesizer_node,
    citation_enforcement_node,
    critic_node,
    writer_node,
    graceful_degrade_node
)


def _get_max_parallel_searches() -> int:
    """Read MAX_PARALLEL_SEARCHES from env. Defaults to 5 (planner targets 3-5 sub-queries)."""
    raw = os.getenv("MAX_PARALLEL_SEARCHES", "5")
    try:
        value = int(raw)
    except ValueError:
        print(f"MAX_PARALLEL_SEARCHES={raw!r} is not a valid integer, falling back to 5")
        return 5
    if value < 1:
        print(f"MAX_PARALLEL_SEARCHES={value} must be >= 1, falling back to 5")
        return 5
    return value


def build_graph():
    """
    Full Step 2 pipeline with parallel search via Send().
    """
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("searcher", search_node)
    workflow.add_node("merge_searches", merge_search_results)
    workflow.add_node("memory_rag", memory_rag_node)
    workflow.add_node("synthesizer", synthesizer_node)
    workflow.add_node("citation_enforcement", citation_enforcement_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("writer", writer_node)
    workflow.add_node("graceful_degrade", graceful_degrade_node)
    
    # Entry point
    workflow.set_entry_point("planner")
    
    # Planner -> parallel searchers using Send()
    def route_to_searches(state: AgentState):
        """Send each sub-query to a parallel search agent."""
        if not state.plan or not state.plan.sub_queries:
            return "merge_searches"
        
        max_searches = _get_max_parallel_searches()
        sub_queries = state.plan.sub_queries
        if len(sub_queries) > max_searches:
            print(f"PLANNER: plan has {len(sub_queries)} sub-queries, "
                  f"only searching the first {max_searches} "
                  f"(set MAX_PARALLEL_SEARCHES in .env to change)")
        
        sends = []
        for sq in sub_queries[:max_searches]:
            # Pass sub-query as research_question in a partial state
            sends.append(Send("searcher", AgentState(
                run_id=state.run_id,
                research_question=sq.query,  # The search agent uses this as the query
                plan=state.plan
            )))
        
        return sends
    
    workflow.add_conditional_edges("planner", route_to_searches)
    
    # Searchers -> merge -> memory_rag
    workflow.add_edge("searcher", "merge_searches")
    workflow.add_edge("merge_searches", "memory_rag")
    
    # Memory RAG -> synthesizer
    workflow.add_edge("memory_rag", "synthesizer")
    
    # Synthesizer -> citation enforcement
    workflow.add_edge("synthesizer", "citation_enforcement")
    
    # Citation enforcement routing
    def route_after_citation(state: AgentState):
        if state.declined:
            return "writer"
        return "critic"
    
    workflow.add_conditional_edges(
        "citation_enforcement",
        route_after_citation,
        {"writer": "writer", "critic": "critic"}
    )
    
    # Critic routing
    def route_after_critic(state: AgentState):
        if not state.critique:
            return "writer"
        
        threshold = state.plan.quality_threshold if state.plan else 0.75
        
        if state.critique.quality_score >= threshold:
            return "writer"
        
        if state.retry_count < 2:
            return "planner"
        else:
            return "graceful_degrade"
    
    workflow.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "writer": "writer",
            "planner": "planner",
            "graceful_degrade": "graceful_degrade"
        }
    )
    
    # Writer and graceful degrade -> END
    workflow.add_edge("writer", END)
    workflow.add_edge("graceful_degrade", END)
    
    return workflow.compile()


graph = build_graph()