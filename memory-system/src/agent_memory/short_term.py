"""Report p9: five pending turns, fifteen context turns, token pressure first."""

import asyncio

from .models import ExtractionWindow, Scope
from .ports import ModelNotConfigured
from .retrieval import estimate_tokens


class ShortTermMemory:
    def __init__(self, store, settings, model=None):
        self.store, self.settings, self.model = store, settings, model

    async def window(self, scope: Scope, session_id: str) -> ExtractionWindow:
        session = await asyncio.to_thread(self.store.get_session, scope, session_id)
        turns = await asyncio.to_thread(self.store.turns, scope, session_id)
        pending = [t for t in turns if t.sequence > session.processed_sequence]
        new_turns = []
        # Prompt/schema overhead is intentionally reserved; count is a conservative estimate.
        budget = self.settings.window_token_limit - estimate_tokens(session.model_dump_json()) - 1000
        if budget <= 0:
            raise ValueError("Session state exceeds extraction budget; shorten state before extracting")
        for turn in pending[: self.settings.pending_turns]:
            cost = estimate_tokens(turn.model_dump_json())
            if cost > budget:
                if not new_turns:
                    raise ValueError(
                        "Single turn exceeds extraction budget; split large tool output into turns"
                    )
                break
            new_turns.append(turn)
            budget -= cost
        context = []
        previous = [t for t in turns if t.sequence <= session.processed_sequence]
        for turn in reversed(previous[-self.settings.context_turns :]):
            cost = estimate_tokens(turn.model_dump_json())
            if cost > budget:
                break
            context.insert(0, turn)
            budget -= cost
        return ExtractionWindow(session=session, new_turns=new_turns, context_turns=context)

    async def extract(self, scope: Scope, session_id: str):
        window = await self.window(scope, session_id)
        if not window.new_turns:
            return {"status": "idle", "processed_sequence": window.session.processed_sequence, "memories": []}
        if self.model is None:
            raise ModelNotConfigured("Configure a model before extracting; pending turns are preserved")
        async with asyncio.timeout(self.settings.model_timeout):
            result = await self.model.extract(window)
        memories = await asyncio.to_thread(
            self.store.commit_extraction, scope, window.session, window.new_turns, result
        )
        return {
            "status": "processed",
            "processed_sequence": window.new_turns[-1].sequence,
            "memories": memories,
        }
