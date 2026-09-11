"""Shared contracts. Changes here should be reviewed by all module owners."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Contract):
    tenant_id: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=100)


class Event(Contract):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=16000)
    occurred_at: datetime = Field(default_factory=utcnow)

    @field_validator("occurred_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include timezone")
        return value.astimezone(UTC)


class TurnInput(Contract):
    request_id: str = Field(min_length=1, max_length=100)
    events: list[Event] = Field(min_length=1, max_length=30)


class Turn(TurnInput):
    turn_id: str
    session_id: str
    sequence: int


class Session(Contract):
    session_id: str
    topic: str = ""
    project_id: str | None = None
    goal: str = ""
    plan: list[str] = Field(default_factory=list)
    pending_tasks: list[str] = Field(default_factory=list)
    summary: str = ""
    processed_sequence: int = 0
    revision: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Evidence(Contract):
    turn_id: str
    event_index: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=16000)


class Candidate(Contract):
    content: str = Field(min_length=1, max_length=3000)
    kind: Literal["fact", "preference", "event", "procedure"] = "fact"
    subject: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[Evidence] = Field(min_length=1, max_length=30)
    assertion: Literal["stated", "observed", "planned", "hypothetical", "inferred"] = "stated"
    durable: bool = False
    scope_type: Literal["session", "project", "user"] = "session"
    # Organizational hints; never owner/permission controls or mandatory query gates.
    hierarchy_path: list[str] = Field(default_factory=list, max_length=3)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def aware_validity(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("valid times must include timezone")
        return value.astimezone(UTC) if value else None


class Memory(Candidate):
    memory_id: str
    version: int = 1
    session_id: str
    scope_id: str
    tier: Literal["short", "long"] = "short"
    status: Literal["active", "superseded", "retracted", "pending"] = "active"
    supersedes: str | None = None
    recorded_at: datetime = Field(default_factory=utcnow)


class StatePatch(Contract):
    goal: str | None = None
    plan: list[str] | None = None
    pending_tasks: list[str] | None = None


class ExtractionWindow(Contract):
    session: Session
    new_turns: list[Turn]
    context_turns: list[Turn]


class ExtractionResult(Contract):
    candidates: list[Candidate] = Field(default_factory=list, max_length=40)
    summary: str = Field(default="", max_length=3000)
    state_patch: StatePatch = Field(default_factory=StatePatch)


class QueryPlan(Contract):
    route: Literal["none", "short", "long", "both"] = "both"
    semantic_queries: list[str] = Field(default_factory=list, max_length=3)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    required_info: list[str] = Field(default_factory=list, max_length=20)
    depth: int = Field(default=3, ge=1, le=20)
    subject: str | None = None
    predicate: str | None = None
    temporal_mode: Literal["current", "history"] = "current"
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("as_of must include timezone")
        return value.astimezone(UTC) if value else None


class Hit(Contract):
    content: str
    score: float = Field(ge=0, le=1)
    source_file: str
    chunk_id: str
    engine: str = "simple_memory"
    metadata: dict = Field(default_factory=dict)


class SearchResponse(Contract):
    results: list[Hit]
    plan: QueryPlan
    mode: str
    candidate_count: int
    selected_k: int
    context_tokens: int
    warnings: list[str] = Field(default_factory=list)


class AnswerResponse(Contract):
    generated_text: str
    citations: list[int]
    sources: list[Hit]
    retrieval_count: int
    elapsed_ms: int
    warnings: list[str] = Field(default_factory=list)


class EvolutionInput(Contract):
    action: Literal["promote", "merge", "supersede", "retract", "defer"]
    expected_version: int = Field(ge=1)
    target_id: str | None = None
    target_version: int | None = Field(default=None, ge=1)
    effective_at: datetime | None = None

    @field_validator("effective_at")
    @classmethod
    def aware_effective(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("effective_at must include timezone")
        return value.astimezone(UTC) if value else None
