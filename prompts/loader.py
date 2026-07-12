"""Loads prompt definitions from prompts/v1/*.yaml at runtime.

Until now these YAML files were reference documentation only — the agents
hardcoded their prompts inline in code, so editing a YAML file had no
effect. This loader makes them the actual source of truth: system_template
and user_template are read from here and used to build the LangChain
ChatPromptTemplate, so editing the YAML now changes agent behavior.

Dynamic, per-call content that doesn't belong in a static file (retry gap
analysis, chunk context, sub-query lists) is still injected by each agent
at call time via template placeholders like {question}, {context}, etc.
"""

import os
import yaml
from functools import lru_cache

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "v1")


@lru_cache(maxsize=None)
def load_prompt(name: str) -> dict:
    """Load prompts/v1/{name}.yaml. Cached after first read per process,
    so a running server doesn't re-read the file on every agent call —
    restart the process to pick up YAML edits.
    """
    path = os.path.join(_PROMPTS_DIR, f"{name}.yaml")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "system_template" not in data or "user_template" not in data:
        raise ValueError(
            f"prompts/v1/{name}.yaml must be a mapping with at least "
            f"'system_template' and 'user_template' keys"
        )

    return data