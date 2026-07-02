import os
import json
import redis
from typing import Optional, List, Dict, Any


class RedisClient:
    """Redis client for run state, SSE events, and caching."""
    
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.client = redis.from_url(self.url, decode_responses=True)
        self.ttl = 86400  # 24 hours
    
    def save_state(self, run_id: str, state: dict):
        """Save run state as JSON."""
        key = f"run:{run_id}:state"
        self.client.setex(key, self.ttl, json.dumps(state, default=str))
    
    def get_state(self, run_id: str) -> Optional[dict]:
        """Get run state."""
        key = f"run:{run_id}:state"
        data = self.client.get(key)
        return json.loads(data) if data else None
    
    def save_status(self, run_id: str, status: str):
        """Save run status."""
        key = f"run:{run_id}:status"
        self.client.setex(key, self.ttl, status)
    
    def get_status(self, run_id: str) -> Optional[str]:
        """Get run status."""
        key = f"run:{run_id}:status"
        return self.client.get(key)
    
    def add_event(self, run_id: str, event: dict):
        """Append SSE event to list, in chronological order."""
        key = f"run:{run_id}:events"
        self.client.rpush(key, json.dumps(event, default=str))
        self.client.expire(key, self.ttl)
    
    def get_events(self, run_id: str, since: int = 0) -> List[dict]:
        """Get SSE events from index `since` onward (chronological order)."""
        key = f"run:{run_id}:events"
        events = self.client.lrange(key, since, -1)
        return [json.loads(e) for e in events]
    
    def cache_search(self, query_hash: str, results: dict, ttl: int = 21600):
        """Cache search results (6 hours)."""
        key = f"search:cache:{query_hash}"
        self.client.setex(key, ttl, json.dumps(results, default=str))
    
    def get_cached_search(self, query_hash: str) -> Optional[dict]:
        """Get cached search results."""
        key = f"search:cache:{query_hash}"
        data = self.client.get(key)
        return json.loads(data) if data else None
    
    def health_check(self) -> bool:
        """Check Redis connection."""
        try:
            return self.client.ping()
        except:
            return False


# Singleton
redis_client = RedisClient()


def emit_event(run_id: str, event_type: str, data: Optional[dict] = None):
    """Push a pipeline event onto the run's SSE log.

    Shared by api/routes.py (pipeline-level events: run_started, done,
    declined, error) and graph/nodes.py (per-node events: node_start,
    node_complete, node_error) so both go through the same event log
    without nodes.py needing to import from api/.
    """
    import time
    event = {
        "type": event_type,
        "run_id": run_id,
        "timestamp": time.time(),
        **(data or {})
    }
    redis_client.add_event(run_id, event)