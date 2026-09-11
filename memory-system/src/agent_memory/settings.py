"""Small explicit configuration; .env files are not silently loaded."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("MEMORY_DB_PATH", "data/memory.sqlite3")))
    local_tenant: str = field(default_factory=lambda: os.getenv("MEMORY_LOCAL_TENANT", "local"))
    local_owner: str = field(default_factory=lambda: os.getenv("MEMORY_LOCAL_OWNER", "demo"))
    pending_turns: int = 5
    context_turns: int = 15
    window_token_limit: int = 6000
    prompt_token_limit: int = 10000
    retrieval_token_limit: int = 3000
    max_top_k: int = 20
    max_candidates: int = 120
    activation_ttl_seconds: int = 300
    model_timeout: float = 30.0
