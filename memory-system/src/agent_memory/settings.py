"""Small explicit configuration; .env files are not silently loaded."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Local developer settings. Existing process environment always wins.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


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
    model_timeout: float = field(default_factory=lambda: _positive_float("MEMORY_MODEL_TIMEOUT", 30.0))
    model_max_tokens: int = field(default_factory=lambda: _positive_int("MEMORY_MODEL_MAX_TOKENS", 4096))
