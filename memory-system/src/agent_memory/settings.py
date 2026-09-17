"""Validated configuration; process environment overrides the local project .env."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Local developer settings. Existing process environment always wins.
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
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
    api_key: str = field(default_factory=lambda: os.getenv("MEMORY_API_KEY", ""), repr=False)
    deployment_mode: str = field(default_factory=lambda: os.getenv("MEMORY_DEPLOYMENT_MODE", "local"))
    acceleration_enabled: bool = field(
        default_factory=lambda: (
            os.getenv("MEMORY_ACCELERATION_ENABLED", "true").lower() in {"1", "true", "yes"}
        )
    )
    inference_cache_ttl_seconds: float = field(
        default_factory=lambda: _positive_float("MEMORY_INFERENCE_CACHE_TTL", 60)
    )
    inference_cache_max_entries: int = field(
        default_factory=lambda: _positive_int("MEMORY_INFERENCE_CACHE_ENTRIES", 128)
    )

    def __post_init__(self):
        for name in (
            "pending_turns",
            "window_token_limit",
            "prompt_token_limit",
            "retrieval_token_limit",
            "max_top_k",
            "max_candidates",
            "activation_ttl_seconds",
            "model_max_tokens",
            "inference_cache_max_entries",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.context_turns, bool)
            or not isinstance(self.context_turns, int)
            or self.context_turns < 0
        ):
            raise ValueError("context_turns must be a non-negative integer")
        if not math.isfinite(self.model_timeout) or self.model_timeout <= 0:
            raise ValueError("model_timeout must be finite and positive")
        if not math.isfinite(self.inference_cache_ttl_seconds) or self.inference_cache_ttl_seconds <= 0:
            raise ValueError("inference_cache_ttl_seconds must be finite and positive")
        if self.deployment_mode not in {"local", "production"}:
            raise ValueError("MEMORY_DEPLOYMENT_MODE must be local or production")
        if self.deployment_mode == "production" and len(self.api_key) < 32:
            raise ValueError("Production HTTP deployment requires MEMORY_API_KEY of at least 32 characters")
        for name in ("local_tenant", "local_owner"):
            value = getattr(self, name)
            if not value.strip() or len(value) > 100:
                raise ValueError(f"{name} must contain 1–100 characters")
