"""Single-node SQLite persistence. All public methods are synchronous and thread-safe.

Application services call these through asyncio.to_thread. The JSON bodies keep the
initial schema small; indexed ownership, order and lifecycle fields stay relational.
"""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .evidence_policy import explicit_persistence_request, question_only
from .models import (
    AnswerContext,
    AnswerResponse,
    Candidate,
    EvolutionInput,
    ExtractionResult,
    Memory,
    Scope,
    Session,
    Turn,
    TurnInput,
    new_id,
    utcnow,
)
from .ports import ConflictError, NotFoundError


class SQLiteStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, id TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(tenant, owner, id)
            );
            CREATE TABLE IF NOT EXISTS turns (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, session TEXT NOT NULL,
                id TEXT NOT NULL, sequence INTEGER NOT NULL, request_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(tenant, owner, id),
                UNIQUE(tenant, owner, session, request_id),
                UNIQUE(tenant, owner, session, sequence)
            );
            CREATE TABLE IF NOT EXISTS memories (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, id TEXT NOT NULL,
                version INTEGER NOT NULL, session TEXT NOT NULL, tier TEXT NOT NULL,
                status TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(tenant, owner, id, version)
            );
            CREATE INDEX IF NOT EXISTS memory_owner ON memories(tenant, owner, tier, status);
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, owner TEXT NOT NULL,
                memory_id TEXT NOT NULL, action TEXT NOT NULL, recorded_at TEXT NOT NULL,
                before_json TEXT, after_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS summaries (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, group_key TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(tenant, owner, group_key)
            );
            CREATE TABLE IF NOT EXISTS evolution_jobs (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, session TEXT NOT NULL,
                id TEXT PRIMARY KEY, memory_ids TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS answers (
                tenant TEXT NOT NULL, owner TEXT NOT NULL, session TEXT NOT NULL,
                id TEXT PRIMARY KEY, content TEXT NOT NULL, context TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS answer_owner ON answers(tenant, owner, session);
            PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        with self._lock:
            nested = self._db.in_transaction
            savepoint = new_id("sp")
            self._db.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                if nested:
                    self._db.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    self._db.commit()
            except BaseException:
                if nested:
                    self._db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._db.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    self._db.rollback()
                raise

    @staticmethod
    def _who(scope: Scope) -> tuple[str, str]:
        return scope.tenant_id, scope.owner_id

    def close(self):
        with self._lock:
            self._db.close()

    def ping(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1").fetchone()[0] == 1

    def backup_to(self, destination: Path) -> None:
        """Create a consistent online SQLite backup without overwriting existing files.

        Run through asyncio.to_thread; keep backup paths in trusted operator code,
        never expose arbitrary destination paths as an HTTP endpoint.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also prevents overwriting the live database.
        with destination.open("xb"):
            pass
        try:
            with self._lock:
                target = sqlite3.connect(str(destination))
                try:
                    self._db.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Backup failed SQLite integrity check")
                finally:
                    target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def create_session(self, scope: Scope, topic: str = "", project_id: str | None = None) -> Session:
        session = Session(session_id=new_id("session"), topic=topic, project_id=project_id)
        with self.transaction():
            self._db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (*self._who(scope), session.session_id, session.model_dump_json()),
            )
        return session

    def get_session(self, scope: Scope, session_id: str) -> Session:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM sessions WHERE tenant=? AND owner=? AND id=?",
                (*self._who(scope), session_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Session not found")
            return Session.model_validate_json(row["body"])

    def sessions(self, scope):
        with self._lock:
            return [
                Session.model_validate_json(row["body"])
                for row in self._db.execute(
                    "SELECT body FROM sessions WHERE tenant=? AND owner=? ORDER BY rowid", self._who(scope)
                ).fetchall()
            ]

    def _save_session(self, scope: Scope, session: Session):
        self._db.execute(
            "UPDATE sessions SET body=? WHERE tenant=? AND owner=? AND id=?",
            (session.model_dump_json(), *self._who(scope), session.session_id),
        )

    def save_answer(
        self,
        scope: Scope,
        session_id: str,
        answer: AnswerResponse,
        run_events: list[dict],
        expected_version: tuple[int, int] | None = None,
        expected_valid_until: datetime | None = None,
    ) -> str:
        """Retain up to 200 unbound receipts per owner; bound snapshots live in turns.

        A delayed first append can fail if its receipt has been evicted. Successful
        appends remain retryable via request_id regardless of receipt retention.
        """
        context = AnswerContext.model_validate(
            {**answer.model_dump(include=set(AnswerContext.model_fields)), "run_events": run_events}
        )
        answer_id = new_id("answer")
        with self.transaction():
            self.get_session(scope, session_id)
            if (
                expected_version is not None
                and self.generation_version(scope, session_id) != expected_version
            ):
                raise ConflictError("Memory changed before answer receipt could be saved; retry")
            if expected_valid_until is not None and utcnow() >= expected_valid_until:
                raise ConflictError("Memory validity changed before answer receipt could be saved; retry")
            self._db.execute(
                "INSERT INTO answers VALUES (?, ?, ?, ?, ?, ?)",
                (*self._who(scope), session_id, answer_id, answer.generated_text, context.model_dump_json()),
            )
            self._db.execute(
                "DELETE FROM answers WHERE tenant=? AND owner=? AND id NOT IN "
                "(SELECT id FROM answers WHERE tenant=? AND owner=? ORDER BY rowid DESC LIMIT 200)",
                (*self._who(scope), *self._who(scope)),
            )
        return answer_id

    def append_turn(self, scope: Scope, session_id: str, data: TurnInput) -> Turn:
        for event in data.events:
            if event.answer_context is not None:
                raise ValueError("answer_context is server-managed and cannot be submitted")
            if event.answer_id is not None and event.role != "assistant":
                raise ValueError("Only assistant events can reference answer_id")
        # occurred_at generated by Pydantic is not part of identity unless explicitly sent.
        canonical = json.dumps(
            data.model_dump(mode="json", exclude_unset=True), ensure_ascii=False, sort_keys=True
        )
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.transaction():
            session = self.get_session(scope, session_id)
            row = self._db.execute(
                "SELECT body, request_hash FROM turns WHERE tenant=? AND owner=? AND session=? AND request_id=?",
                (*self._who(scope), session_id, data.request_id),
            ).fetchone()
            if row:
                if row["request_hash"] != digest:
                    raise ConflictError("request_id reused with different content")
                return Turn.model_validate_json(row["body"])
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0)+1 FROM turns WHERE tenant=? AND owner=? AND session=?",
                (*self._who(scope), session_id),
            ).fetchone()[0]
            turn = Turn(**data.model_dump(), turn_id=new_id("turn"), session_id=session_id, sequence=sequence)
            for event in turn.events:
                if event.answer_id is None:
                    continue
                receipt = self._db.execute(
                    "SELECT content, context FROM answers WHERE tenant=? AND owner=? AND session=? AND id=?",
                    (*self._who(scope), session_id, event.answer_id),
                ).fetchone()
                if receipt is None:
                    raise ConflictError(
                        "Answer receipt unavailable, expired, or already bound; generate again"
                    )
                if receipt["content"] != event.content:
                    raise ValueError("Assistant content does not match its answer receipt")
                event.answer_context = AnswerContext.model_validate_json(receipt["context"])
                self._db.execute("DELETE FROM answers WHERE id=?", (event.answer_id,))
            self._db.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *self._who(scope),
                    session_id,
                    turn.turn_id,
                    sequence,
                    data.request_id,
                    digest,
                    turn.model_dump_json(),
                ),
            )
            session.revision += 1
            self._save_session(scope, session)
        return turn

    def turns(self, scope: Scope, session_id: str) -> list[Turn]:
        with self._lock:
            self.get_session(scope, session_id)
            rows = self._db.execute(
                "SELECT body FROM turns WHERE tenant=? AND owner=? AND session=? ORDER BY sequence",
                (*self._who(scope), session_id),
            ).fetchall()
            return [Turn.model_validate_json(r["body"]) for r in rows]

    def session_snapshot(self, scope: Scope, session_id: str) -> tuple[Session, list[Turn]]:
        """Read watermark and turns in one SQLite snapshot, including across workers."""
        with self.transaction():
            return self.get_session(scope, session_id), self.turns(scope, session_id)

    def generation_version(self, scope: Scope, session_id: str) -> tuple[int, int]:
        """Revision fence for responses generated outside the database transaction."""
        with self.transaction():
            session = self.get_session(scope, session_id)
            audit_version = self._db.execute(
                "SELECT COALESCE(MAX(id), 0) FROM audit WHERE tenant=? AND owner=?",
                self._who(scope),
            ).fetchone()[0]
            return session.revision, audit_version

    def delete_owner(self, scope: Scope) -> dict[str, int]:
        """Erase canonical data and all derived/audit copies for one authenticated owner."""
        with self.transaction():
            return {
                table: self._db.execute(
                    f"DELETE FROM {table} WHERE tenant=? AND owner=?", self._who(scope)
                ).rowcount
                for table in (
                    "answers",
                    "evolution_jobs",
                    "summaries",
                    "audit",
                    "memories",
                    "turns",
                    "sessions",
                )
            }

    def _check_evidence(self, scope: Scope, session: Session, item: Candidate, new_ids: set[str] | None):
        if item.scope_type == "project" and not session.project_id:
            raise ValueError("Project memory requires a project session")
        if item.valid_from and item.valid_to and item.valid_from >= item.valid_to:
            raise ValueError("Memory valid_from must be before valid_to")
        roles = []
        factual_sources = []
        for ref in item.evidence:
            row = self._db.execute(
                "SELECT body FROM turns WHERE tenant=? AND owner=? AND session=? AND id=?",
                (*self._who(scope), session.session_id, ref.turn_id),
            ).fetchone()
            if not row:
                raise ValueError("Evidence is not in this session")
            turn = Turn.model_validate_json(row["body"])
            if ref.event_index >= len(turn.events) or ref.quote not in turn.events[ref.event_index].content:
                raise ValueError("Evidence quote/index does not match source")
            roles.append(turn.events[ref.event_index].role)
            factual_sources.append(
                turn.events[ref.event_index].role != "assistant"
                and not question_only(turn.events[ref.event_index].content)
            )
        if new_ids is not None and not any(e.turn_id in new_ids for e in item.evidence):
            raise ValueError("New memories must cite at least one pending turn")
        if item.assertion in {"stated", "observed"} and set(roles) == {"assistant"}:
            raise ValueError("Assistant output alone cannot establish a user or observed fact")
        if item.assertion in {"stated", "observed"} and all(
            question_only(ref.quote) for ref in item.evidence
        ):
            raise ValueError("Questions alone cannot establish affirmative facts")
        if item.assertion in {"stated", "observed"} and not any(factual_sources):
            raise ValueError("Questions and assistant replies cannot establish affirmative facts")

    def _save_memory(self, scope: Scope, memory: Memory):
        previous = self._db.execute(
            "SELECT body FROM memories WHERE tenant=? AND owner=? AND id=? AND version=?",
            (*self._who(scope), memory.memory_id, memory.version),
        ).fetchone()
        if previous:
            memory.revision = Memory.model_validate_json(previous["body"]).revision + 1
        self._db.execute(
            "INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *self._who(scope),
                memory.memory_id,
                memory.version,
                memory.session_id,
                memory.tier,
                memory.status,
                memory.model_dump_json(),
            ),
        )

    def _record(self, scope: Scope, action: str, after: Memory, before: Memory | None = None):
        self._db.execute(
            "INSERT INTO audit(tenant,owner,memory_id,action,recorded_at,before_json,after_json) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                *self._who(scope),
                after.memory_id,
                action,
                utcnow().isoformat(),
                before.model_dump_json() if before else None,
                after.model_dump_json(),
            ),
        )
        # Summaries are disposable organizational views, never canonical facts.
        self._db.execute("DELETE FROM summaries WHERE tenant=? AND owner=?", self._who(scope))

    def _add_candidate(
        self,
        scope: Scope,
        session: Session,
        item: Candidate,
        new_ids=None,
        direct_long_term: bool = False,
    ) -> Memory:
        self._check_evidence(scope, session, item, new_ids)
        scope_id = {"user": scope.owner_id, "project": session.project_id, "session": session.session_id}[
            item.scope_type
        ]
        tier = (
            "long"
            if (
                direct_long_term
                and item.durable
                and item.scope_type in {"user", "project"}
                and item.assertion not in {"inferred", "hypothetical"}
                and not self._has_unresolved_long_conflict(scope, item, scope_id)
            )
            else "short"
        )
        for existing in self.memories(scope, None if tier == "long" else session.session_id, tier):
            if (
                existing.status == "active"
                and existing.content == item.content
                and existing.predicate == item.predicate
                and existing.subject == item.subject
                and existing.value == item.value
                and existing.scope_type == item.scope_type
                and existing.scope_id == scope_id
                and existing.valid_from == item.valid_from
                and existing.valid_to == item.valid_to
                and existing.kind == item.kind
                and existing.assertion == item.assertion
                and existing.durable == item.durable
            ):
                merged = list(
                    {
                        (e.turn_id, e.event_index, e.quote): e for e in existing.evidence + item.evidence
                    }.values()
                )
                updated = existing.model_copy(update={"evidence": merged[-30:]})
                self._save_memory(scope, updated)
                self._record(scope, "merge_evidence", updated, existing)
                return updated
        memory = Memory(
            **item.model_dump(),
            memory_id=new_id("mem"),
            session_id=session.session_id,
            scope_id=scope_id,
            tier=tier,
        )
        self._save_memory(scope, memory)
        self._record(scope, "extract", memory)
        return memory

    def _has_unresolved_long_conflict(self, scope: Scope, item: Candidate, scope_id: str) -> bool:
        for existing in self.memories(scope, tier="long"):
            if (
                existing.status == "active"
                and existing.scope_type == item.scope_type
                and existing.scope_id == scope_id
                and existing.subject == item.subject
                and existing.predicate == item.predicate
                and existing.value != item.value
            ):
                return True
        return False

    def add_candidates(self, scope: Scope, session_id: str, candidates: list[Candidate]) -> list[Memory]:
        with self.transaction():
            session = self.get_session(scope, session_id)
            return [self._add_candidate(scope, session, item) for item in candidates]

    def commit_extraction(
        self, scope: Scope, session: Session, new_turns: list[Turn], result: ExtractionResult
    ) -> list[Memory]:
        with self.transaction():
            latest = self.get_session(scope, session.session_id)
            if latest.revision != session.revision:
                raise ConflictError("Session changed during extraction; retry from the saved watermark")
            pending = [
                turn
                for turn in self.turns(scope, session.session_id)
                if turn.sequence > latest.processed_sequence
            ]
            if not new_turns or new_turns != pending[: len(new_turns)]:
                raise ValueError("Extraction must commit a non-empty contiguous prefix of pending turns")
            explicit_long_term_request = any(
                event.role == "user" and explicit_persistence_request(event.content)
                for turn in new_turns
                for event in turn.events
            )
            items = [
                self._add_candidate(
                    scope,
                    latest,
                    c,
                    {t.turn_id for t in new_turns},
                    direct_long_term=explicit_long_term_request,
                )
                for c in result.candidates
            ]
            latest.summary = result.summary
            for key, value in result.state_patch.model_dump(exclude_none=True).items():
                setattr(latest, key, value)
            latest.processed_sequence = new_turns[-1].sequence
            latest.revision += 1
            self._save_session(scope, latest)
            if items:
                self._db.execute(
                    "INSERT INTO evolution_jobs VALUES(?,?,?,?,?)",
                    (
                        *self._who(scope),
                        session.session_id,
                        new_id("job"),
                        json.dumps([m.memory_id for m in items]),
                    ),
                )
            return items

    def evolution_jobs(self, scope, session_id):
        with self._lock:
            self.get_session(scope, session_id)
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT id,memory_ids FROM evolution_jobs WHERE tenant=? AND owner=? AND session=? ORDER BY rowid",
                    (*self._who(scope), session_id),
                ).fetchall()
            ]

    def finish_evolution(self, scope, job_id):
        with self.transaction():
            self._db.execute(
                "DELETE FROM evolution_jobs WHERE tenant=? AND owner=? AND id=?", (*self._who(scope), job_id)
            )

    def memories(self, scope: Scope, session_id: str | None = None, tier: str | None = None) -> list[Memory]:
        with self._lock:
            sql = "SELECT body FROM memories WHERE tenant=? AND owner=?"
            args: list = list(self._who(scope))
            if session_id is not None:
                sql += " AND session=?"
                args.append(session_id)
            if tier is not None:
                sql += " AND tier=?"
                args.append(tier)
            rows = self._db.execute(sql + " ORDER BY rowid", args).fetchall()
            return [Memory.model_validate_json(r["body"]) for r in rows]

    def get_memory(self, scope: Scope, memory_id: str) -> Memory:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM memories WHERE tenant=? AND owner=? AND id=? ORDER BY version DESC LIMIT 1",
                (*self._who(scope), memory_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("Memory not found")
            return Memory.model_validate_json(row["body"])

    @staticmethod
    def _fact_key(memory: Memory):
        return memory.scope_type, memory.scope_id, memory.subject, memory.predicate

    def evolve(self, scope: Scope, memory_id: str, data: EvolutionInput) -> Memory:
        with self.transaction():
            source = self.get_memory(scope, memory_id)
            before_result = source
            allowed_status = {"active", "pending", "archived"} if data.action == "retract" else {"active"}
            if data.action == "restore":
                allowed_status = {"archived"}
            elif data.action == "activate":
                allowed_status = {"pending"}
            if source.version != data.expected_version or source.status not in allowed_status:
                raise ConflictError("Memory version/status changed")
            if data.expected_revision is not None and source.revision != data.expected_revision:
                raise ConflictError("Memory revision changed; reload before applying the action")
            if data.action in {"restore", "activate"}:
                if source.tier == "long" and self._has_unresolved_long_conflict(
                    scope, source, source.scope_id
                ):
                    raise ConflictError("Resolve active conflicting facts before restoring this memory")
                result = source.model_copy(update={"status": "active"})
            elif data.action == "archive":
                result = source.model_copy(update={"status": "archived"})
            elif data.action == "defer":
                result = source.model_copy(update={"status": "pending"})
            elif data.action == "retract":
                result = source.model_copy(update={"status": "retracted"})
            else:
                if (
                    (source.tier != "short" and data.action != "merge")
                    or not source.durable
                    or source.scope_type == "session"
                ):
                    raise ValueError("Promotion requires a durable short memory scoped to user/project")
                if source.assertion in {"hypothetical", "inferred"}:
                    raise ValueError("Unverified inference/hypothesis cannot be promoted")
                target = self.get_memory(scope, data.target_id) if data.target_id else None
                if data.action == "promote":
                    if target:
                        raise ValueError("promote does not accept a target")
                    collisions = [
                        m
                        for m in self.memories(scope, tier="long")
                        if m.status == "active" and self._fact_key(m) == self._fact_key(source)
                    ]
                    if collisions:
                        raise ConflictError(
                            "Related long memory exists; decide merge or supersede explicitly"
                        )
                    result = source.model_copy(update={"tier": "long"})
                else:
                    if target is None or data.target_version is None:
                        raise ValueError("merge/supersede requires target_id and target_version")
                    if (
                        target.version != data.target_version
                        or target.status != "active"
                        or target.tier != "long"
                    ):
                        raise ConflictError("Target version/status changed")
                    if data.target_revision is not None and target.revision != data.target_revision:
                        raise ConflictError("Target revision changed")
                    if self._fact_key(target) != self._fact_key(source):
                        raise ValueError("Cannot replace a different fact or scope")
                    if source.memory_id == target.memory_id:
                        raise ValueError("Evolution source and target must differ")
                    before_result = target
                    if data.action == "merge":
                        if (
                            target.value != source.value
                            or target.assertion != source.assertion
                            or target.kind != source.kind
                            or target.valid_from != source.valid_from
                            or target.valid_to != source.valid_to
                        ):
                            raise ValueError(
                                "Scaffold merge only accepts identical facts; semantic merge needs review"
                            )
                        evidence = list(
                            {
                                (e.turn_id, e.event_index, e.quote): e
                                for e in target.evidence + source.evidence
                            }.values()
                        )
                        result = target.model_copy(update={"evidence": evidence[-30:]})
                    elif data.action == "correct":
                        old = target.model_copy(update={"status": "retracted"})
                        self._save_memory(scope, old)
                        self._record(scope, "correct_retract", old, target)
                        result = source.model_copy(
                            update={
                                "memory_id": target.memory_id,
                                "version": target.version + 1,
                                "tier": "long",
                                "supersedes": f"{target.memory_id}:{target.version}",
                            }
                        )
                    else:
                        t = data.effective_at
                        if t is None:
                            raise ValueError("supersede requires an explicit effective_at")
                        if target.valid_to is not None or (target.valid_from and t <= target.valid_from):
                            raise ValueError("Only forward changes to open-ended facts are supported")
                        if source.valid_to and t >= source.valid_to:
                            raise ValueError("New validity interval must be non-empty")
                        old = target.model_copy(update={"status": "superseded", "valid_to": t})
                        self._save_memory(scope, old)
                        self._record(scope, "close_validity", old, target)
                        result = source.model_copy(
                            update={
                                "memory_id": target.memory_id,
                                "version": target.version + 1,
                                "tier": "long",
                                "valid_from": t,
                                "supersedes": f"{target.memory_id}:{target.version}",
                            }
                        )
                    consumed = source.model_copy(update={"status": "superseded"})
                    self._save_memory(scope, consumed)
                    self._record(scope, "consume_candidate", consumed, source)
            self._save_memory(scope, result)
            self._record(scope, data.action, result, before_result)
            return result

    def save_group_synthesis(self, scope, path, scope_type, scope_id, synthesis):
        """Publish a derived summary only if its exact source versions remain current."""
        with self.transaction():
            key = json.dumps([scope_type, scope_id, path], ensure_ascii=False)
            row = self._db.execute(
                "SELECT body FROM summaries WHERE tenant=? AND owner=? AND group_key=?",
                (*self._who(scope), key),
            ).fetchone()
            if row is None:
                raise ConflictError("Group changed during synthesis; rebuild before retry")
            group = json.loads(row["body"])
            payload = (
                synthesis.model_dump(mode="json") if hasattr(synthesis, "model_dump") else dict(synthesis)
            )
            if not payload.get("source_refs") or not set(payload["source_refs"]) <= set(group["memory_refs"]):
                raise ValueError("Synthesis sources must belong to current group")
            now = utcnow()
            for ref in group["memory_refs"]:
                mid, version = ref.rsplit(":", 1)
                memory = self.get_memory(scope, mid)
                if (
                    memory.version != int(version)
                    or memory.status != "active"
                    or (memory.valid_from and memory.valid_from > now)
                    or (memory.valid_to and memory.valid_to <= now)
                ):
                    raise ConflictError("Summary source no longer active")
            group["synthesis"] = {**payload, "derived": True, "usable_as_evidence": False}
            self._db.execute(
                "UPDATE summaries SET body=? WHERE tenant=? AND owner=? AND group_key=?",
                (json.dumps(group, ensure_ascii=False), *self._who(scope), key),
            )
            return group

    def reconcile_short(self, scope, candidates):
        from .maintenance import correction_relation, same_fact

        actions = []
        with self.transaction():
            for candidate in candidates:
                source = self.get_memory(scope, candidate.memory_id)
                if source.tier != "short" or source.status != "active" or source.durable:
                    continue
                targets = [
                    m
                    for m in self.memories(scope, source.session_id, "short")
                    if m.status == "active"
                    and m.memory_id != source.memory_id
                    and m.recorded_at < source.recorded_at
                    and self._fact_key(m) == self._fact_key(source)
                    and m.assertion in {"stated", "observed"}
                ]
                if source.assertion not in {"stated", "observed"}:
                    continue
                turns = {turn.turn_id: turn for turn in self.turns(scope, source.session_id)}
                source_times = [
                    turns[e.turn_id].events[e.event_index].occurred_at
                    for e in source.evidence
                    if e.turn_id in turns
                    and e.event_index < len(turns[e.turn_id].events)
                    and turns[e.turn_id].events[e.event_index].role != "assistant"
                ]
                for target in targets:
                    relation = correction_relation(source, target, [e.quote for e in source.evidence])
                    if same_fact(source, target):
                        relation = "merge"
                    if relation in {"correct", "supersede", "merge"}:
                        old_updates = {"status": "retracted" if relation == "correct" else "superseded"}
                        source_updates = {"supersedes": f"{target.memory_id}:{target.version}"}
                        if relation == "supersede":
                            if not source_times:
                                continue
                            effective = source.valid_from or max(source_times)
                            if (
                                (target.valid_from and effective <= target.valid_from)
                                or (target.valid_to and effective >= target.valid_to)
                                or (source.valid_to and effective >= source.valid_to)
                            ):
                                continue
                            old_updates["valid_to"] = effective
                            source_updates["valid_from"] = effective
                        if relation == "merge":
                            combined = list(
                                {
                                    (e.turn_id, e.event_index, e.quote): e
                                    for e in target.evidence + source.evidence
                                }.values()
                            )
                            if len(combined) > 30:
                                continue
                            source_updates["evidence"] = combined
                        old = target.model_copy(update=old_updates)
                        self._save_memory(scope, old)
                        self._record(scope, "short_" + relation, old, target)
                        source = source.model_copy(update=source_updates)
                        self._save_memory(scope, source)
                        self._record(scope, "short_reconcile", source, candidate)
                        actions.append(
                            {"action": relation, "memory_id": source.memory_id, "target_id": target.memory_id}
                        )
        return actions

    def history(self, scope: Scope, memory_id: str) -> list[dict]:
        with self._lock:
            self.get_memory(scope, memory_id)
            return [
                dict(r)
                for r in self._db.execute(
                    "SELECT action,recorded_at,before_json,after_json FROM audit "
                    "WHERE tenant=? AND owner=? AND memory_id=? ORDER BY id",
                    (*self._who(scope), memory_id),
                ).fetchall()
            ]

    def rebuild_groups(self, scope: Scope) -> list[dict]:
        from .maintenance import summary_groups

        with self.transaction():
            previous = {
                json.dumps([g["scope_type"], g["scope_id"], g["path"]], ensure_ascii=False): g
                for g in self.groups(scope)
            }
            self._db.execute("DELETE FROM summaries WHERE tenant=? AND owner=?", self._who(scope))
            groups = summary_groups(self.memories(scope, tier="long"), utcnow())
            for group in groups:
                key = json.dumps([group["scope_type"], group["scope_id"], group["path"]], ensure_ascii=False)
                cached = previous.get(key, {})
                if cached.get("memory_refs") == group["memory_refs"] and cached.get("synthesis"):
                    group["synthesis"] = cached["synthesis"]
                self._db.execute(
                    "INSERT INTO summaries VALUES(?,?,?,?)",
                    (*self._who(scope), key, json.dumps(group, ensure_ascii=False)),
                )
            return groups

    def groups(self, scope: Scope) -> list[dict]:
        with self._lock:
            groups = [
                json.loads(r["body"])
                for r in self._db.execute(
                    "SELECT body FROM summaries WHERE tenant=? AND owner=?", self._who(scope)
                ).fetchall()
            ]
            now = utcnow()
            memories = {}
            valid = []
            for group in groups:
                usable = bool(group.get("memory_refs"))
                for ref in group.get("memory_refs", []):
                    memory_id, version = ref.rsplit(":", 1)
                    if memory_id not in memories:
                        try:
                            memories[memory_id] = self.get_memory(scope, memory_id)
                        except NotFoundError:
                            memories[memory_id] = None
                    memory = memories[memory_id]
                    if (
                        memory is None
                        or memory.version != int(version)
                        or memory.status != "active"
                        or memory.scope_type != group["scope_type"]
                        or memory.scope_id != group["scope_id"]
                        or (memory.valid_from and memory.valid_from > now)
                        or (memory.valid_to and memory.valid_to <= now)
                    ):
                        usable = False
                        break
                if usable:
                    valid.append(group)
            return valid

    def hierarchy(self, scope: Scope) -> dict:
        """Build an H-MEM reference tree from actual long-memory versions.

        Domain/category/trace labels come from extraction hints or deterministic
        fallbacks. Episode leaves remain the canonical versioned memories.
        """
        kind_names = {"fact": "事实", "preference": "偏好", "event": "事件", "procedure": "经验"}
        tree: dict[str, dict] = {}
        # The hierarchy is the active long-term view; retracted and superseded
        # versions remain available through history/audit, not as live memory.
        now = utcnow()
        versions = [
            memory
            for memory in self.memories(scope, tier="long")
            if memory.status == "active"
            and (memory.valid_from is None or memory.valid_from <= now)
            and (memory.valid_to is None or now < memory.valid_to)
        ]
        for memory in versions:
            path = [item.strip() for item in memory.hierarchy_path if item.strip()]
            domain = path[0] if path else kind_names.get(memory.kind, memory.kind)
            category = path[1] if len(path) > 1 else memory.subject
            trace = path[2] if len(path) > 2 else memory.predicate
            domain_node = tree.setdefault(domain, {"name": domain, "categories": {}})
            category_node = domain_node["categories"].setdefault(category, {"name": category, "traces": {}})
            trace_node = category_node["traces"].setdefault(trace, {"name": trace, "episodes": []})
            trace_node["episodes"].append(
                {
                    "memory_id": memory.memory_id,
                    "version": memory.version,
                    "content": memory.content,
                    "kind": memory.kind,
                    "status": memory.status,
                    "scope_type": memory.scope_type,
                    "scope_id": memory.scope_id,
                    "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
                    "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
                    "recorded_at": memory.recorded_at.isoformat(),
                    "evidence_count": len(memory.evidence),
                    "source_session_id": memory.session_id,
                }
            )

        domains = []
        for domain in tree.values():
            categories = []
            for category in domain["categories"].values():
                traces = []
                for trace in category["traces"].values():
                    trace["episodes"].sort(key=lambda item: (item["memory_id"], item["version"]))
                    trace["episode_count"] = len(trace["episodes"])
                    trace["active_count"] = sum(e["status"] == "active" for e in trace["episodes"])
                    traces.append(trace)
                traces.sort(key=lambda item: item["name"])
                category["traces"] = traces
                category["episode_count"] = sum(t["episode_count"] for t in traces)
                categories.append(category)
            categories.sort(key=lambda item: item["name"])
            domain["categories"] = categories
            domain["episode_count"] = sum(c["episode_count"] for c in categories)
            domains.append(domain)
        domains.sort(key=lambda item: item["name"])
        return {
            "schema": "hmem-reference/v1",
            "organization_mode": "domain_category_trace_episode",
            "summary_mode": "labels_from_memory_extraction",
            "domain_count": len(domains),
            "episode_count": len(versions),
            "domains": domains,
        }
