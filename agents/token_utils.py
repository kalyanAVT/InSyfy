"""Shared helper for extracting token usage from LLM responses.

Used by planner.py, synthesizer.py, and critic.py — the three agents that
call an LLM directly (as opposed to writer.py, searcher.py, memory_rag.py,
which don't).
"""

import os


def extract_token_count(ai_message) -> int:
    """Best-effort extraction of total token count from an AIMessage.

    Tries, in order:
    1. `ai_message.usage_metadata["total_tokens"]` — the standard
       langchain-core field (populated by recent langchain-groq /
       langchain-openai versions).
    2. `ai_message.response_metadata["token_usage"]["total_tokens"]` or
       `["usage"]["total_tokens"]` — provider-specific fallback locations
       some integrations use instead.

    Returns 0 if none of these are present (e.g. an older provider
    integration, or the call failed before a response was returned) rather
    than raising — token accounting is a nice-to-have, and should never be
    the reason an agent call fails.
    """
    usage = getattr(ai_message, "usage_metadata", None)
    if isinstance(usage, dict) and usage.get("total_tokens") is not None:
        return int(usage["total_tokens"])

    meta = getattr(ai_message, "response_metadata", None) or {}
    for key in ("token_usage", "usage"):
        block = meta.get(key)
        if isinstance(block, dict) and block.get("total_tokens") is not None:
            return int(block["total_tokens"])

    return 0


def estimate_cost(token_usage: dict) -> float:
    """Estimate USD cost from a {agent_name: total_tokens} dict.

    Uses a single blended COST_PER_1K_TOKENS rate from the environment
    rather than a hardcoded provider price — actual per-token pricing
    varies by provider, model, and plan, and changes over time. Defaults
    to 0.0 (no cost estimate reported) if the rate isn't set, rather than
    silently assuming a number that could be wrong.

    Set COST_PER_1K_TOKENS in .env to your actual provider rate to get
    real cost figures out of the metrics endpoint.
    """
    rate = float(os.getenv("COST_PER_1K_TOKENS", "0") or 0)
    if rate <= 0:
        return 0.0
    total_tokens = sum(token_usage.values()) if token_usage else 0
    return round((total_tokens / 1000) * rate, 6)