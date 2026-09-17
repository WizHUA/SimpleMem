"""Report p9: five pending turns, fifteen context turns, token pressure first."""

import asyncio
import json

from .models import ExtractionWindow, ReferenceFact, Scope, utcnow
from .ports import ModelNotConfigured
from .retrieval import _terms, estimate_tokens


class ShortTermMemory:
    def __init__(self, store, settings, model=None):
        self.store, self.settings, self.model = store, settings, model

    async def window(self, scope: Scope, session_id: str) -> ExtractionWindow:
        session, turns = await asyncio.to_thread(self.store.session_snapshot, scope, session_id)
        pending = [t for t in turns if t.sequence > session.processed_sequence]
        new_turns = []
        # Prompt/schema overhead is intentionally reserved; count is a conservative estimate.
        budget = self.settings.window_token_limit - estimate_tokens(session.model_dump_json()) - 1000
        if budget <= 0:
            raise ValueError("Session state exceeds extraction budget; shorten state before extracting")
        for turn in pending[: self.settings.pending_turns]:
            cost = estimate_tokens(json.dumps(turn.prompt_dump(), ensure_ascii=False))
            if cost > budget:
                if not new_turns:
                    raise ValueError(
                        "Single turn exceeds extraction budget; split large tool output into turns"
                    )
                break
            new_turns.append(turn)
            budget -= cost
        references = []
        if new_turns:
            long = await asyncio.to_thread(self.store.memories, scope, None, "long")
            now = utcnow()
            query_terms = _terms(" ".join(event.content for turn in new_turns for event in turn.events))
            ranked = []
            for memory in long:
                if (
                    memory.status != "active"
                    or (memory.valid_from and memory.valid_from > now)
                    or (memory.valid_to and memory.valid_to <= now)
                ):
                    continue
                if memory.scope_type == "project" and memory.scope_id != session.project_id:
                    continue
                if memory.scope_type not in {"project", "user"}:
                    continue
                overlap = len(query_terms & _terms(memory.subject + " " + memory.predicate))
                if overlap:
                    ranked.append((overlap, memory))
            reference_budget = min(budget, 1200)
            seen = set()
            for _, memory in sorted(ranked, key=lambda item: (-item[0], item[1].memory_id)):
                identity = (memory.scope_type, memory.subject, memory.predicate)
                if identity in seen:
                    continue
                ref = ReferenceFact(
                    subject=memory.subject,
                    predicate=memory.predicate,
                    value=memory.value,
                    scope_type=memory.scope_type,
                )
                cost = estimate_tokens(ref.model_dump_json()) + 2
                if cost > reference_budget:
                    continue
                references.append(ref)
                seen.add(identity)
                reference_budget -= cost
                budget -= cost
                if len(references) == 8:
                    break
        context = []
        previous = [t for t in turns if t.sequence <= session.processed_sequence]
        for turn in reversed(previous[-self.settings.context_turns :] if self.settings.context_turns else []):
            cost = estimate_tokens(json.dumps(turn.prompt_dump(), ensure_ascii=False))
            if cost > budget:
                break
            context.insert(0, turn)
            budget -= cost
        return ExtractionWindow(
            session=session, new_turns=new_turns, context_turns=context, reference_fields=references
        )

    async def extract(self, scope: Scope, session_id: str):
        window = await self.window(scope, session_id)
        if not window.new_turns:
            return {"status": "idle", "processed_sequence": window.session.processed_sequence, "memories": []}
        if self.model is None:
            raise ModelNotConfigured("Configure a model before extracting; pending turns are preserved")
        async with asyncio.timeout(self.settings.model_timeout):
            result = await self.model.extract(window)
        next_session = window.session.model_copy(
            update={
                "summary": result.summary,
                **result.state_patch.model_dump(exclude_none=True),
            }
        )
        # Reject model-generated state before persisting a state that would make the
        # next extraction permanently impossible. Preserve pending turns on failure.
        if estimate_tokens(next_session.model_dump_json()) > self.settings.window_token_limit // 3:
            raise ValueError("Extracted session state exceeds budget; model must return a shorter state")
        memories = await asyncio.to_thread(
            self.store.commit_extraction, scope, window.session, window.new_turns, result
        )
        return {
            "status": "processed",
            "processed_sequence": window.new_turns[-1].sequence,
            "summary": result.summary,
            "candidate_count": len(result.candidates),
            "memories": memories,
        }
