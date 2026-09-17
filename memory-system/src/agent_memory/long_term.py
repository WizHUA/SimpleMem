"""Transactional lifecycle decisions grounded in original user evidence."""

import asyncio

from .maintenance import consolidation_key, correction_relation, fact_key, retention, same_fact
from .models import EvolutionInput, Scope, utcnow
from .ports import ConflictError


class LongTermMemory:
    def __init__(self, store, model=None):
        self.store = store
        self.model = model

    async def evolve(self, scope: Scope, memory_id: str, command: EvolutionInput):
        return await asyncio.to_thread(self.store.evolve, scope, memory_id, command)

    def _user_evidence(self, scope, memory):
        turns = {t.turn_id: t for t in self.store.turns(scope, memory.session_id)}
        events = []
        for ref in memory.evidence:
            turn = turns.get(ref.turn_id)
            if turn is None or ref.event_index >= len(turn.events):
                continue
            event = turn.events[ref.event_index]
            if event.role == "user" and ref.quote in event.content:
                events.append((ref.quote, event.occurred_at))
        return events

    def _process(self, scope, memories):
        applied, review = [], []
        for candidate in memories:
            with self.store.transaction():
                source = self.store.get_memory(scope, candidate.memory_id)
                if (
                    source.status != "active"
                    or source.tier != "short"
                    or not source.durable
                    or source.scope_type == "session"
                    or source.assertion not in {"stated", "observed"}
                ):
                    continue
                evidence = self._user_evidence(scope, source)
                if not evidence:
                    review.append({"memory_id": source.memory_id, "reason": "no_user_evidence"})
                    continue
                targets = [
                    m
                    for m in self.store.memories(scope, tier="long")
                    if m.status == "active" and fact_key(m) == fact_key(source)
                ]
                action, target, effective_at = "promote", None, None
                if targets:
                    identical = [m for m in targets if same_fact(source, m)]
                    if len(targets) == 1 and identical:
                        action, target = "merge", identical[0]
                    elif len(targets) == 1:
                        target = targets[0]
                        action = correction_relation(source, target, [q for q, _ in evidence])
                        if action == "supersede":
                            effective_at = source.valid_from or max(t for _, t in evidence)
                            if target.valid_to or (target.valid_from and effective_at <= target.valid_from):
                                action = None
                    else:
                        action = None
                if action is None:
                    self.store.evolve(
                        scope,
                        source.memory_id,
                        EvolutionInput(
                            action="defer",
                            expected_version=source.version,
                        ),
                    )
                    review.append(
                        {
                            "memory_id": source.memory_id,
                            "reason": "conflicting_values_require_review",
                            "target_ids": [m.memory_id for m in targets],
                            "suggested_actions": ["activate", "supersede", "retract"],
                        }
                    )
                    continue
                result = self.store.evolve(
                    scope,
                    source.memory_id,
                    EvolutionInput(
                        action=action,
                        expected_version=source.version,
                        target_id=target.memory_id if target else None,
                        target_version=target.version if target else None,
                        effective_at=effective_at,
                    ),
                )
                applied.append(
                    {
                        "action": action,
                        "memory_id": result.memory_id,
                        "source_id": source.memory_id,
                        "target_id": target.memory_id if target else None,
                        "version": result.version,
                    }
                )
        return {"applied": applied, "review": review, "warnings": []}

    async def process_extraction(self, scope: Scope, memories):
        report = await asyncio.to_thread(self._process, scope, memories)
        deadline = asyncio.get_running_loop().time() + 30
        if self.model and hasattr(self.model, "relate"):
            for item in report["review"][:8]:
                if not item.get("target_ids"):
                    continue
                try:
                    source = await asyncio.to_thread(self.store.get_memory, scope, item["memory_id"])
                    targets = [
                        await asyncio.to_thread(self.store.get_memory, scope, mid)
                        for mid in item["target_ids"]
                    ]
                    async with asyncio.timeout_at(deadline):
                        proposal = await self.model.relate(source, targets)
                    data = proposal.model_dump() if hasattr(proposal, "model_dump") else proposal
                    quotes = {e.quote for e in source.evidence}
                    if (
                        not data.get("evidence_quotes")
                        or data.get("target_id") not in item["target_ids"]
                        or not all(
                            quote and any(quote in original for original in quotes)
                            for quote in data.get("evidence_quotes", [])
                        )
                    ):
                        raise ValueError("Relation proposal references unsupported evidence/target")
                    item["model_proposal"] = data
                    item["automatically_applied"] = False
                except Exception as exc:  # noqa: BLE001 - optional provider must not undo persisted extraction
                    report["warnings"].append(f"relation_model_unavailable:{type(exc).__name__}")
                    if asyncio.get_running_loop().time() >= deadline:
                        break
        return report

    def _maintain(self, scope):
        applied, review = [], []
        with self.store.transaction():
            decisions = [retention(m, utcnow()) for m in self.store.memories(scope)]
            for item in decisions:
                if item["recommendation"] == "archive_expired_event":
                    self.store.evolve(
                        scope,
                        item["memory_id"],
                        EvolutionInput(
                            action="archive",
                            expected_version=item["version"],
                        ),
                    )
                    applied.append({"action": "archive", "memory_id": item["memory_id"]})
                elif item["recommendation"] == "review_archive":
                    review.append(item)
            canonical = {}
            for memory in self.store.memories(scope, tier="long"):
                if memory.status != "active":
                    continue
                key = consolidation_key(memory)
                target = canonical.get(key)
                if target is None:
                    canonical[key] = memory
                    continue
                try:
                    self.store.evolve(
                        scope,
                        memory.memory_id,
                        EvolutionInput(
                            action="merge",
                            expected_version=memory.version,
                            target_id=target.memory_id,
                            target_version=target.version,
                        ),
                    )
                except (ConflictError, ValueError) as exc:
                    review.append({"memory_id": memory.memory_id, "reason": str(exc)})
                else:
                    applied.append(
                        {"action": "merge", "memory_id": target.memory_id, "source_id": memory.memory_id}
                    )
            groups = self.store.rebuild_groups(scope)
        return {
            "status": "ok",
            "group_count": len(groups),
            "groups": groups,
            "mode": "evidence_bound_maintenance",
            "semantic_synthesis": "extractive_with_sources",
            "applied": applied,
            "review": review,
            "retention": decisions,
            "warnings": [],
            "forgetting_policy": "recoverable_archive_no_automatic_fact_deletion",
        }

    async def maintain(self, scope: Scope):
        report = await asyncio.to_thread(self._maintain, scope)
        deadline = asyncio.get_running_loop().time() + 30
        if self.model and hasattr(self.model, "summarize"):
            for group in report["groups"][:8]:
                if group.get("synthesis"):
                    continue
                try:
                    memories = [
                        await asyncio.to_thread(self.store.get_memory, scope, ref.rsplit(":", 1)[0])
                        for ref in group["memory_refs"][:12]
                    ]
                    async with asyncio.timeout_at(deadline):
                        synthesis = await self.model.summarize(memories)
                    data = synthesis.model_dump() if hasattr(synthesis, "model_dump") else synthesis
                    if not data.get("source_refs") or not set(data["source_refs"]) <= {
                        f"{m.memory_id}:{m.version}" for m in memories
                    }:
                        raise ValueError("Summary references unsupported memory versions")
                    data = {**data, "coverage_count": len(memories), "group_count": len(group["memory_refs"])}
                    await asyncio.to_thread(
                        self.store.save_group_synthesis,
                        scope,
                        group["path"],
                        group["scope_type"],
                        group["scope_id"],
                        data,
                    )
                    group["synthesis"] = {
                        **data,
                        "is_derived_view": True,
                        "usable_as_extraction_evidence": False,
                        "coverage_count": len(memories),
                        "group_count": len(group["memory_refs"]),
                    }
                except Exception as exc:  # noqa: BLE001 - derived summaries are a best-effort provider boundary
                    report["warnings"].append(f"summary_model_unavailable:{type(exc).__name__}")
                    if asyncio.get_running_loop().time() >= deadline:
                        break
        if any(group.get("synthesis") for group in report["groups"]):
            report["semantic_synthesis"] = "model_assisted_with_sources"
        return report
