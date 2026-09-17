"""Intent-planned STM-first retrieval with evidence joins and hierarchy hints.

This small-data implementation scans caller-supplied memories. Candidate budgets
bound retained search results, not database scan work. An injected embedder enables
real cosine search; without one the baseline uses lexical and structured evidence.
"""

import asyncio
import json
import math
import os
import re
from collections import Counter, defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4
from weakref import WeakValueDictionary

from .models import Hit, Memory, QueryPlan, QueryStep, SearchResponse, Session, utcnow
from .ports import Embedder, MemoryModel
from .providers import ModelOutputError
from .retrieval_projection import RetrievalProjection
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
    support: tuple[str, ...] = ()
    rank_score: float | None = None
    conflict_pending: list[dict] = field(default_factory=list)


class Retriever:
    def __init__(
        self,
        settings: Settings,
        model: MemoryModel | None = None,
        embedder: Embedder | None = None,
        projection: RetrievalProjection | None = None,
    ):
        self.settings = settings
        self.model = model
        self.embedder = embedder
        self.projection = projection or RetrievalProjection()
        self._projection_context = ContextVar("retrieval_projection_context", default=("default", 0))
        self._projection_locks = WeakValueDictionary()
        self._embedding_instance_id = uuid4().hex

    @staticmethod
    def projection_namespace(tenant: str, owner: str, session: str = "") -> str:
        return json.dumps([tenant, owner, session], ensure_ascii=False)

    def clear_projection(self, namespace: str) -> None:
        self.projection.clear(namespace)

    def clear_owner(self, tenant: str, owner: str) -> None:
        self.projection.clear_owner(tenant, owner)

    def close(self) -> None:
        self.projection.close()

    async def search(
        self,
        query: str,
        session: Session,
        short: list[Memory],
        long: list[Memory],
        top_k: int = 10,
        timeout: float = 30,
        projection_scope: str = "default",
        projection_generation: int | None = None,
        progress=None,
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
        generation = (
            self.projection.generation(projection_scope)
            if projection_generation is None
            else projection_generation
        )
        token = self._projection_context.set((projection_scope, generation))
        lock = self._projection_locks.setdefault(projection_scope, asyncio.Lock())
        try:
            async with asyncio.timeout(timeout), lock:
                await asyncio.to_thread(
                    self.projection.sync, projection_scope, short + long, _terms, generation
                )
                return await self._search(query, session, short, long, top_k, plan, progress)
        finally:
            self._projection_context.reset(token)

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
        progress=None,
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

        if progress:
            await progress(
                "planning",
                "查询已规划，开始查找相关记忆",
                plan=plan.model_dump(mode="json"),
                steps=[step.model_dump(mode="json") for step in steps],
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
        if progress:
            await progress(
                "retrieval",
                f"短期记忆召回 {len(stm)} 条候选",
                steps=[step.model_dump(mode="json") for step in steps],
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
            if progress:
                await progress(
                    "retrieval",
                    f"长期记忆补充 {len(ltm)} 条候选，正在筛选证据",
                    steps=[step.model_dump(mode="json") for step in steps],
                )
            warnings.append("long_term_searched")
            if plan.route == "short":
                warnings.append("short_evidence_uncertain: one long-term supplement performed")
        else:
            warnings.append("short_only: exact single-field lookup; no long-term search")

        pool = await asyncio.to_thread(
            self._expand_entities,
            query,
            plan,
            session,
            short + (long if need_long else []),
            pool,
            candidate_cap,
            now,
        )
        linked = sum(bool(hit.support) for hit in pool)
        if linked:
            steps.append(
                QueryStep(
                    order=len(steps) + 1,
                    phase="filter",
                    action="evidence_entity_expansion",
                    input_count=len(short) + (len(long) if need_long else 0),
                    output_count=linked,
                    detail="max_hops=2; explicit_fact_edges; support_sources_required; no inferred identity",
                )
            )

        # A factual correction/exception is a relation, never an in-place mutation.
        overrides = self._local_overrides(pool, plan)
        if overrides:
            warnings.append("local_override: session/project exception does not change long-term facts")
        for hit in pool:
            hit.rank_score = self._rank_score(hit, plan, now)
        ranked = sorted(
            pool,
            key=lambda hit: (
                hit.memory.memory_id not in overrides,
                -hit.rank_score,
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
                detail=f"local_overrides={len(overrides)}; rerank=bounded_age_decay; facts_and_preferences_protected=true",
            )
        )
        selected, used = self._select(ranked, plan, target)
        conflicts = self._pending_conflicts(selected, short + long, session, plan, now)
        if conflicts:
            warnings.append(
                "pending_conflict_requires_confirmation: " + json.dumps(conflicts, ensure_ascii=False)
            )
        steps.append(
            QueryStep(
                order=len(steps) + 1,
                phase="selection",
                action="dynamic_k_and_token_budget",
                input_count=len(ranked),
                output_count=len(selected),
                detail=f"target={target}; context_tokens={used}; evidence_chain_bundles=true",
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
        if not cls._applicable(memory, session) or memory.status in ("retracted", "pending", "archived"):
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
        terms = _terms(" ".join([query, *plan.keywords, *plan.required_info, *plan.semantic_queries]))
        namespace, generation = self._projection_context.get()
        lexical_keys = set(
            await asyncio.to_thread(
                self.projection.candidates,
                namespace,
                terms,
                eligible,
                max(limit * 4, 24),
                generation,
            )
        )
        lexical_eligible = [m for m in eligible if self.projection.key(m) in lexical_keys]
        warnings.append(f"fts5_projection: reranked={len(lexical_eligible)}; eligible={len(eligible)}")
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
            documents = [
                " ".join([m.content, m.subject, m.predicate, m.value, *m.keywords]) for m in lexical_eligible
            ]
            scores = self._bm25(terms, documents)
            for memory, score in zip(lexical_eligible, scores):
                if score > 0:
                    add(memory, score, "lexical")
                path_score = _overlap(terms, " ".join(memory.hierarchy_path))
                if path_score:
                    add(memory, 0.4 * path_score, "hierarchy")
            # Exact symbolic matches survive lexical candidate truncation.
            for memory in eligible:
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
            vectors, reused = await self._projected_vectors(queries, eligible, namespace, generation)
            warnings.append(f"vector_projection: reused={reused}; documents={len(eligible)}")

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

    async def _projected_vectors(self, queries, memories, namespace, generation):
        endpoint, model = getattr(self.embedder, "endpoint", None), getattr(self.embedder, "model", None)
        identity = [endpoint, model, os.getenv("MEMORY_EMBEDDING_REVISION", "1")]
        if endpoint is None or model is None:
            identity.append(self._embedding_instance_id)
        provider = self.projection.digest(json.dumps(identity))
        digests = [self.projection.digest(memory.content) for memory in memories]
        cached = await asyncio.to_thread(self.projection.vectors, namespace, provider, digests, generation)
        missing = {
            digest: memory.content for digest, memory in zip(digests, memories) if digest not in cached
        }
        encoded = await self.embedder.encode(queries + list(missing.values()))
        if len(encoded) != len(queries) + len(missing):
            raise ValueError("embedding count mismatch")
        fresh = dict(zip(missing, encoded[len(queries) :]))
        if fresh:
            await asyncio.to_thread(self.projection.save_vectors, namespace, provider, fresh, generation)
        vectors = cached | fresh
        reused = sum(digest in cached for digest in digests)
        return encoded[: len(queries)] + [vectors[digest] for digest in digests], reused

    @staticmethod
    def _bm25(terms: set[str], documents: list[str]) -> list[float]:
        """BM25 with Chinese bigrams and English words; bounded score is not confidence."""
        counters = []
        for text in documents:
            counter: Counter[str] = Counter()
            for segment in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
                allowed = _terms(segment)
                if re.search(r"[\u4e00-\u9fff]", segment):
                    tokens = [segment[i : i + 2] for i in range(len(segment) - 1)]
                    tokens.extend(char for char in segment if char in allowed)
                    counter.update(token for token in tokens if token in allowed)
                else:
                    counter.update(allowed)
            counters.append(counter)
        if not counters:
            return []
        lengths = [sum(c.values()) for c in counters]
        average = max(1, sum(lengths) / len(lengths))
        frequencies = Counter(term for c in counters for term in c)
        result = []
        for counter, length in zip(counters, lengths):
            score = 0.0
            for term in terms & counter.keys():
                inverse = math.log(1 + (len(counters) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                tf = counter[term]
                score += inverse * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / average))
            result.append(score / (1 + score))
        return result

    @classmethod
    def _expand_entities(cls, query, plan, session, memories, pool, cap, now):
        """Bounded exact entity joins, not a claim that connected entities are identical.

        Only explicit alias predicates are reversible. Other relations are directed
        subject -> value. Every traversed edge is returned as evidence with the leaf.
        """
        broad = plan.model_copy(update={"subject": None, "predicate": None})
        eligible = [m for m in memories if cls._eligible(m, session, broad, now)]
        factual = [m for m in eligible if m.assertion in ("stated", "observed") and m.kind == "fact"]
        subjects: dict[str, list[Memory]] = defaultdict(list)
        for memory in eligible:
            subjects[memory.subject.casefold().strip()].append(memory)
        aliases = {"别名", "昵称", "绰号", "又名", "英文名", "中文名", "alias", "nickname", "also known as"}
        edges: dict[str, list[tuple[str, Memory]]] = defaultdict(list)
        for memory in factual:
            source, target = memory.subject.casefold().strip(), memory.value.casefold().strip()
            if not target or len(target) > 80 or re.search(r"[。？！?！\n;；]", target):
                continue
            if memory.predicate.casefold().strip() in aliases:
                edges[source].append((target, memory))
                edges[target].append((source, memory))
            elif target in subjects and target != source:
                edges[source].append((target, memory))

        def mentioned(name):
            if not name or len(name) < 2:
                return False
            if re.fullmatch(r"[a-z0-9_ ]+", name):
                return (
                    re.search(r"(?<![a-z0-9_])" + re.escape(name) + r"(?![a-z0-9_])", query.casefold())
                    is not None
                )
            return name in query.casefold()

        frontier = [(name, ()) for name in sorted(edges) if mentioned(name)]
        found = {(h.memory.memory_id, h.memory.version): h for h in pool}
        by_id = {m.memory_id: m for m in eligible}
        visited = set()
        terms = _terms(query + " " + " ".join(plan.required_info))
        additions = []
        for _ in range(2):
            following = []
            for name, path in frontier[:cap]:
                if name in visited:
                    continue
                visited.add(name)
                for target, edge in edges[name][:cap]:
                    support = tuple(dict.fromkeys((*path, edge.memory_id)))
                    following.append((target, support))
                    for memory in subjects.get(target, [])[:cap]:
                        if memory.memory_id in support:
                            continue
                        relevance = _overlap(terms, f"{memory.predicate} {memory.value} {memory.content}")
                        if not relevance:
                            continue
                        additions.append(
                            _Match(memory, min(0.95, 0.6 + relevance * 0.3), {"entity_link"}, support)
                        )
            frontier = following
        for hit in sorted(additions, key=lambda h: (-h.score, len(h.support), h.memory.memory_id)):
            bundle = [by_id[mid] for mid in hit.support] + [hit.memory]
            extra = sum((m.memory_id, m.version) not in found for m in bundle)
            if len(found) + extra > cap:
                protected = {mid for existing in found.values() for mid in existing.support}
                protected.update(m.memory_id for m in bundle)
                removable = sorted(
                    (
                        key
                        for key, existing in found.items()
                        if existing.memory.memory_id not in protected
                        and existing.score < hit.score
                        and not existing.support
                    ),
                    key=lambda key: (found[key].score, key),
                )
                required = len(found) + extra - cap
                if len(removable) < required:
                    continue
                for key in removable[:required]:
                    del found[key]
            for memory in bundle[:-1]:
                key = (memory.memory_id, memory.version)
                if key not in found:
                    found[key] = _Match(memory, hit.score * 0.9, {"entity_link_support"})
            key = (hit.memory.memory_id, hit.memory.version)
            if key not in found:
                found[key] = hit
            else:
                found[key].views.add("entity_link")
                found[key].score = max(found[key].score, hit.score)
                if not mentioned(hit.memory.subject.casefold().strip()):
                    found[key].support = hit.support
        return list(found.values())

    @staticmethod
    def _rank_score(hit: _Match, plan: QueryPlan, now: datetime) -> float:
        """Age only breaks relevance ties; never deletes or hides stable facts."""
        memory = hit.memory
        if plan.temporal_mode == "history" or plan.as_of or memory.kind in ("fact", "preference"):
            return hit.score
        age_days = max(0, (now - memory.recorded_at).total_seconds() / 86400)
        half_life = 90 if memory.tier == "long" else 7
        return hit.score * (0.95 + 0.05 * 2 ** (-age_days / half_life))

    @classmethod
    def _pending_conflicts(cls, selected, memories, session, plan, now):
        """Expose unresolved contradictions as warnings, never as established facts."""
        if plan.temporal_mode == "history" or plan.as_of:
            return []
        broad = plan.model_copy(update={"subject": None, "predicate": None})
        pending = [
            m
            for m in memories
            if m.status == "pending"
            and cls._eligible(m.model_copy(update={"status": "active"}), session, broad, now)
        ]
        summaries = {}
        for hit in selected:
            memory = hit.memory
            for candidate in pending:
                if (candidate.subject.casefold(), candidate.predicate.casefold()) != (
                    memory.subject.casefold(),
                    memory.predicate.casefold(),
                ) or candidate.value.casefold() == memory.value.casefold():
                    continue
                if candidate.memory_id not in summaries and len(summaries) >= 3:
                    continue
                summary = {
                    "memory_id": candidate.memory_id,
                    "status": "pending",
                    "content": candidate.content[:400],
                    "subject": candidate.subject,
                    "predicate": candidate.predicate,
                    "value": candidate.value[:200],
                    "source_turn_ids": list(dict.fromkeys(e.turn_id for e in candidate.evidence))[:3],
                    "interpretation": "unconfirmed alternative; do not assert either value without qualification",
                }
                summaries[candidate.memory_id] = summary
                hit.conflict_pending.append(summary)
        return list(summaries.values())

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
            by_id = {h.memory.memory_id: h for h in ranked}
            bundle = [by_id[mid] for mid in hit.support if mid in by_id] + [hit]
            bundle = [h for h in bundle if h not in selected]
            if len(selected) + len(bundle) > target:
                continue
            cost = sum(estimate_tokens(self._prompt_fragment(h.memory)) for h in bundle)
            if used + cost > self.settings.retrieval_token_limit:
                continue
            selected.extend(bundle)
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
                "score_kind": "max_normalized_bm25_cosine_symbolic_hierarchy_entity_link",
                "support_memory_ids": list(hit.support),
                "rank_score": hit.rank_score if hit.rank_score is not None else hit.score,
                "age_decay_applied": hit.rank_score is not None and hit.rank_score < hit.score,
                "conflict_pending": hit.conflict_pending,
                "recorded_at": memory.recorded_at.isoformat(),
                "evidence": [e.model_dump(mode="json") for e in memory.evidence],
            },
        )
