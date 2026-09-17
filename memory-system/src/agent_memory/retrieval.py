"""Intent-planned STM-first retrieval; H-MEM paths are organizational metadata.

This small-data implementation scans caller-supplied memories. Candidate budgets
bound retained search results, not database scan work. An injected embedder enables
real cosine search; without one the explicitly labelled baseline is lexical only.
"""

import asyncio
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from .models import Hit, Memory, QueryPlan, QueryStep, SearchResponse, Session, utcnow
from .ports import Embedder, MemoryModel
from .providers import ModelOutputError
from .settings import Settings


def estimate_tokens(text: str) -> int:
    """Conservative character-count budget proxy, not an actual tokenizer count."""
    return len(text)


def _terms(text: str) -> set[str]:
    stop = {"the", "is", "a", "an", "what", "was", "of", "my", "me", "please", "and"}
    result: set[str] = set()
    for piece in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if not re.search(r"[\u4e00-\u9fff]", piece):
            if piece not in stop:
                result.add(piece)
            continue
        for phrase in ("请问", "告诉我", "是什么", "什么", "多少", "刚才", "这次", "之前", "现在", "通常"):
            piece = piece.replace(phrase, " ")
        for segment in re.split(r"[ 的地得吗呢啊]+", piece):
            if len(segment) == 1:
                result.add(segment)
            else:
                result.update(segment[i : i + 2] for i in range(len(segment) - 1))
    return result


def _overlap(query_terms: set[str], content: str) -> float:
    return len(query_terms & _terms(content)) / len(query_terms) if query_terms else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a or any(not math.isfinite(v) for v in a + b):
        raise ValueError("invalid embedding vectors")
    norm = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / norm)) if norm else 0.0


@dataclass
class _Match:
    memory: Memory
    score: float
    views: set[str] = field(default_factory=set)


class Retriever:
    def __init__(
        self, settings: Settings, model: MemoryModel | None = None, embedder: Embedder | None = None
    ):
        self.settings = settings
        self.model = model
        self.embedder = embedder

    async def search(
        self,
        query: str,
        session: Session,
        short: list[Memory],
        long: list[Memory],
        top_k: int = 10,
        timeout: float = 30,
    ) -> SearchResponse:
        """Read-only search. TimeoutError means the shared query deadline expired.

        Caller must enforce tenant/owner isolation before providing either list.
        User scope is therefore accepted here; session/project scopes are checked.
        """
        plan = self._rule_plan(query, short)
        if not query.strip() or top_k <= 0:
            return SearchResponse(
                results=[],
                plan=plan,
                mode="hybrid" if self.embedder else "lexical_baseline",
                candidate_count=0,
                selected_k=0,
                context_tokens=0,
                steps=[],
            )
        if timeout <= 0:
            raise TimeoutError("query deadline expired")
        async with asyncio.timeout(timeout):
            return await self._search(query, session, short, long, top_k, plan)

    @staticmethod
    def _rule_plan(query: str, short: list[Memory]) -> QueryPlan:
        recent = bool(re.search(r"刚才|这次|本次|当前会话|just now|this time", query, re.IGNORECASE))
        history = bool(re.search(r"历史|之前|上次|过去|previous|histor|as of", query, re.IGNORECASE))
        compare = bool(re.search(r"相比|比较|对比|区别|compare|difference", query, re.IGNORECASE))
        route = "both" if compare else "short" if recent else "long" if history else "both"
        # Only literal field mentions identify a single field in the baseline.
        pairs = {
            (m.subject, m.predicate)
            for m in short
            if m.subject.casefold() in query.casefold() and m.predicate.casefold() in query.casefold()
        }
        subject, predicate = next(iter(pairs)) if len(pairs) == 1 else (None, None)
        depth = 8 if compare else 6 if re.search(r"总结|汇总|哪些|summari|list", query, re.IGNORECASE) else 3
        return QueryPlan(
            route=route,
            semantic_queries=[query],
            depth=depth,
            subject=subject,
            predicate=predicate,
            temporal_mode="history" if history else "current",
        )

    async def _search(
        self,
        query: str,
        session: Session,
        short: list[Memory],
        long: list[Memory],
        top_k: int,
        plan: QueryPlan,
    ) -> SearchResponse:
        warnings: list[str] = []
        steps: list[QueryStep] = []
        now = utcnow()
        if self.model:
            try:
                plan = await self.model.plan(
                    query,
                    {
                        "session_id": session.session_id,
                        "project_id": session.project_id,
                        "topic": session.topic,
                        "goal": session.goal,
                        "summary": session.summary[:2000],
                        "query_time": now.isoformat(),
                        "short_memory": [
                            m.content for m in short if self._eligible(m, session, QueryPlan(), now)
                        ][-8:],
                    },
                )
                plan = QueryPlan.model_validate(plan)
            except ModelOutputError:
                # A malformed planner response must not prevent a normal answer;
                # the deterministic planner remains bounded and auditable.
                plan = self._rule_plan(query, short)
                warnings.append("model_planner_schema_error: fell back to rule planner")
        else:
            warnings.append("rule_planner: semantic intent and evidence sufficiency are not verified")
        steps.append(
            QueryStep(
                order=1,
                phase="planning",
                action="intent_plan",
                input_count=1,
                output_count=len(plan.required_info),
                detail=f"route={plan.route}; depth={plan.depth}; mode={plan.temporal_mode}",
            )
        )

        if plan.route == "none":
            return SearchResponse(
                results=[],
                plan=plan,
                mode="hybrid" if self.embedder else "lexical_baseline",
                candidate_count=0,
                selected_k=0,
                context_tokens=0,
                steps=steps,
                warnings=warnings,
            )
        cap = min(top_k, self.settings.max_top_k)
        depth = max(3, min(20, max(plan.depth, len(plan.required_info))))
        target = min(cap, depth)
        candidate_cap = min(self.settings.max_candidates, 6 * depth)
        # STM is inspected first even for long-only plans to expose local exceptions.
        # Preserve an actual long-term quota until STM sufficiency is established.
        stm_limit = min(candidate_cap, max(1, candidate_cap // 3))
        stm, semantic_stm = await self._recall(query, plan, session, short, stm_limit, now, warnings)
        steps.append(
            QueryStep(
                order=len(steps) + 1,
                phase="short_retrieval",
                action="semantic_lexical_symbolic_recall",
                input_count=len(short),
                output_count=len(stm),
                detail=f"candidate_limit={stm_limit}",
            )
        )
        pool = stm
        short_is_exact = self._exact_short_answer(query, plan, stm, short, session, now)
        need_long = plan.route in ("long", "both") or not short_is_exact
        semantic_long = False
        if need_long:
            remaining = max(0, candidate_cap - len(stm))
            ltm, semantic_long = await self._recall(query, plan, session, long, remaining, now, warnings)
            pool = self._merge(stm + ltm)
            steps.append(
                QueryStep(
                    order=len(steps) + 1,
                    phase="long_retrieval",
                    action="semantic_lexical_symbolic_recall",
                    input_count=len(long),
                    output_count=len(ltm),
                    detail=f"candidate_limit={remaining}",
                )
            )
            warnings.append("long_term_searched")
            if plan.route == "short":
                warnings.append("short_evidence_uncertain: one long-term supplement performed")
        else:
            warnings.append("short_only: exact single-field lookup; no long-term search")

        # A factual correction/exception is a relation, never an in-place mutation.
        overrides = self._local_overrides(pool, plan)
        if overrides:
            warnings.append("local_override: session/project exception does not change long-term facts")
        ranked = sorted(
            pool,
            key=lambda hit: (
                hit.memory.memory_id not in overrides,
                -hit.score,
                hit.memory.memory_id,
                -hit.memory.version,
            ),
        )
        steps.append(
            QueryStep(
                order=len(steps) + 1,
                phase="filter",
                action="deduplicate_scope_time_version",
                input_count=len(stm) + (len(ltm) if need_long else 0),
                output_count=len(ranked),
                detail=f"local_overrides={len(overrides)}",
            )
        )
        selected, used = self._select(ranked, plan, target)
        steps.append(
            QueryStep(
                order=len(steps) + 1,
                phase="selection",
                action="dynamic_k_and_token_budget",
                input_count=len(ranked),
                output_count=len(selected),
                detail=f"target={target}; context_tokens={used}",
            )
        )
        if plan.required_info:
            warnings.append("required_info_coverage_unverified: retrieval similarity is not entailment")
        if not selected:
            warnings.append("no_supported_result_within_budget")
        elif used >= self.settings.retrieval_token_limit or len(selected) < min(target, len(ranked)):
            warnings.append("result_count_limited_by_context_budget_or_relevance")
        if re.search(r"全部|所有|every|all records", query, re.IGNORECASE):
            warnings.append("top_k_cannot_guarantee_exhaustive_results")
        if semantic_stm or semantic_long:
            warnings.append("semantic_cosine_threshold_0.35_requires_development_set_calibration")
        return SearchResponse(
            results=[self._hit(hit, overrides) for hit in selected],
            plan=plan,
            mode="hybrid" if self.embedder else "lexical_baseline",
            candidate_count=len(pool),
            selected_k=len(selected),
            context_tokens=used,
            steps=steps,
            warnings=list(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _applicable(memory: Memory, session: Session) -> bool:
        if memory.scope_type == "session":
            return memory.scope_id == session.session_id and memory.session_id == session.session_id
        if memory.scope_type == "project":
            return bool(session.project_id) and memory.scope_id == session.project_id
        return True  # Owner/tenant boundary is a repository responsibility.

    @classmethod
    def _eligible(cls, memory: Memory, session: Session, plan: QueryPlan, now: datetime) -> bool:
        if not cls._applicable(memory, session) or memory.status in ("retracted", "pending"):
            return False
        if plan.subject and memory.subject.casefold() != plan.subject.casefold():
            return False
        if plan.predicate and memory.predicate.casefold() != plan.predicate.casefold():
            return False
        if plan.as_of is None and plan.temporal_mode == "history":
            return True
        at = plan.as_of or now
        if memory.valid_from and memory.valid_from > at:
            return False
        if memory.valid_to and at >= memory.valid_to:
            return False
        # A superseded version can still apply before a scheduled replacement.
        return not (memory.status == "superseded" and memory.valid_to is None)

    async def _recall(
        self,
        query: str,
        plan: QueryPlan,
        session: Session,
        memories: list[Memory],
        limit: int,
        now: datetime,
        warnings: list[str],
    ) -> tuple[list[_Match], bool]:
        if limit <= 0:
            return [], False
        # A single symbolic label is only one recall view for multi-slot queries.
        # Using it as a global gate can hide every other requested fact, even
        # though the planner supplied separate semantic queries/requirements.
        multi_slot = len(plan.required_info) > 1 or len(plan.semantic_queries) > 1
        eligibility_plan = (
            plan.model_copy(update={"subject": None, "predicate": None}) if multi_slot else plan
        )
        if multi_slot and (plan.subject or plan.predicate):
            warnings.append("multi_slot_recall: symbolic labels do not restrict other required fields")
        eligible = await asyncio.to_thread(
            lambda: [m for m in memories if self._eligible(m, session, eligibility_plan, now)]
        )
        if not eligible and (plan.subject or plan.predicate):
            # Planner labels are guesses, not canonical store keys. A synonym must
            # not hide scoped and time-valid memories from lexical/semantic recall.
            broad_plan = plan.model_copy(update={"subject": None, "predicate": None})
            eligible = await asyncio.to_thread(
                lambda: [m for m in memories if self._eligible(m, session, broad_plan, now)]
            )
            if eligible:
                warnings.append("symbolic_field_miss: fell back to scoped recall")
        if not eligible:
            return [], False
        terms = _terms(" ".join([query, *plan.keywords]))
        matches: dict[tuple[str, int], _Match] = {}

        def add(memory: Memory, score: float, view: str) -> None:
            key = (memory.memory_id, memory.version)
            if key in matches:
                matches[key].score = max(matches[key].score, score)
                matches[key].views.add(view)
            else:
                matches[key] = _Match(memory, score, {view})

        def lexical_recall():
            # Scans can be CPU-heavy on larger owner libraries. Keep them off
            # the host SDK event loop; the outer request still owns the deadline.
            for memory in eligible:
                score = _overlap(
                    terms,
                    " ".join(
                        [memory.content, memory.subject, memory.predicate, memory.value, *memory.keywords]
                    ),
                )
                if score > 0:
                    add(memory, score, "lexical")
                if (
                    plan.subject
                    and plan.predicate
                    and memory.subject.casefold() == plan.subject.casefold()
                    and memory.predicate.casefold() == plan.predicate.casefold()
                ):
                    add(memory, 1.0, "symbolic")

        await asyncio.to_thread(lexical_recall)
        semantic_used = False
        if self.embedder:
            queries = [text.strip() for text in plan.semantic_queries if text.strip()] or [query]
            vectors = await self.embedder.encode(queries + [m.content for m in eligible])
            if len(vectors) != len(queries) + len(eligible):
                raise ValueError("embedding count mismatch")

            def semantic_recall():
                semantic = []
                for memory, vector in zip(eligible, vectors[len(queries) :]):
                    cosine = max(_cosine(qv, vector) for qv in vectors[: len(queries)])
                    if cosine >= 0.35:  # Initial recall threshold, not truth/confidence.
                        semantic.append((memory, (cosine + 1.0) / 2.0))
                return semantic

            semantic = await asyncio.to_thread(semantic_recall)
            for memory, score in semantic:
                add(memory, score, "semantic")
            semantic_used = True
        result = sorted(matches.values(), key=lambda hit: (-hit.score, hit.memory.memory_id))[:limit]
        return result, semantic_used

    @staticmethod
    def _merge(matches: list[_Match]) -> list[_Match]:
        unique: dict[tuple[str, int], _Match] = {}
        for hit in matches:
            key = (hit.memory.memory_id, hit.memory.version)
            if key not in unique:
                unique[key] = _Match(hit.memory, hit.score, set(hit.views))
            else:
                unique[key].score = max(unique[key].score, hit.score)
                unique[key].views.update(hit.views)
        return list(unique.values())

    @classmethod
    def _exact_short_answer(
        cls,
        query: str,
        plan: QueryPlan,
        hits: list[_Match],
        all_short: list[Memory],
        session: Session,
        now: datetime,
    ) -> bool:
        # Look at the full eligible field, not just the candidate top-k: a lower
        # ranked contradictory value must not silently make the field "sufficient".
        values = {m.value.casefold() for m in all_short if cls._eligible(m, session, plan, now)}
        remainder = query.casefold()
        for text in (
            plan.subject,
            plan.predicate,
            "刚才",
            "这次",
            "本次",
            "是什么时候",
            "是什么",
            "是多少",
            "是几号",
            "请问",
            "的",
            "what is",
            "just now",
            "this time",
        ):
            if text:
                remainder = remainder.replace(text.casefold(), "")
        only_field = not re.sub(r"[\s？?。.!！]", "", remainder)
        return bool(
            plan.route == "short"
            and plan.subject
            and plan.predicate
            and hits
            and len(plan.required_info) <= 1
            and plan.temporal_mode == "current"
            and len(values) == 1
            and only_field
        )

    @staticmethod
    def _local_overrides(hits: list[_Match], plan: QueryPlan) -> set[str]:
        if plan.temporal_mode == "history" or plan.as_of:
            return set()
        long_fields = {
            (h.memory.subject, h.memory.predicate): h.memory.value for h in hits if h.memory.tier == "long"
        }
        return {
            h.memory.memory_id
            for h in hits
            if h.memory.tier == "short"
            and h.memory.scope_type in ("session", "project")
            and (h.memory.subject, h.memory.predicate) in long_fields
            and long_fields[h.memory.subject, h.memory.predicate] != h.memory.value
        }

    def _select(self, ranked: list[_Match], plan: QueryPlan, target: int) -> tuple[list[_Match], int]:
        # Slot text only diversifies candidates. It does not verify slot coverage.
        ordered: list[_Match] = []
        for slot in plan.required_info:
            terms = _terms(slot)
            possible = [hit for hit in ranked if _overlap(terms, hit.memory.content) > 0]
            if possible:
                best = max(possible, key=lambda hit: (_overlap(terms, hit.memory.content), hit.score))
                if best not in ordered:
                    ordered.append(best)
        ordered.extend(hit for hit in ranked if hit not in ordered)
        selected: list[_Match] = []
        used = 0
        for hit in ordered:
            if len(selected) >= target:
                break
            # Source label, validity and formatting must fit too; no partial facts.
            cost = estimate_tokens(self._prompt_fragment(hit.memory))
            if used + cost > self.settings.retrieval_token_limit:
                continue
            selected.append(hit)
            used += cost
        return selected, used

    @staticmethod
    def _prompt_fragment(memory: Memory) -> str:
        return (
            f"[memory:{memory.memory_id}:{memory.version} tier={memory.tier} status={memory.status} "
            f"valid_from={memory.valid_from} valid_to={memory.valid_to}]\n{memory.content}\n"
        )

    @staticmethod
    def _hit(hit: _Match, overrides: set[str]) -> Hit:
        memory = hit.memory
        return Hit(
            content=memory.content,
            score=hit.score,
            source_file=f"[memory:{memory.memory_id}]",
            chunk_id=f"{memory.memory_id}:{memory.version}",
            metadata={
                "memory_id": memory.memory_id,
                "version": memory.version,
                "tier": memory.tier,
                "assertion": memory.assertion,
                "kind": memory.kind,
                "subject": memory.subject,
                "predicate": memory.predicate,
                "value": memory.value,
                "durable": memory.durable,
                "scope_type": memory.scope_type,
                "scope_id": memory.scope_id,
                "status": memory.status,
                "valid_from": memory.valid_from.isoformat() if memory.valid_from else None,
                "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
                "matched_views": sorted(hit.views),
                "hierarchy_path": list(memory.hierarchy_path),
                "local_override": memory.memory_id in overrides,
                "score_kind": "max_query_term_coverage_or_normalized_cosine_or_exact_field_match",
                "evidence": [e.model_dump(mode="json") for e in memory.evidence],
            },
        )
