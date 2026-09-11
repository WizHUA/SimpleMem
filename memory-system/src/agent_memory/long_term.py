"""Small explicit evolution entry point; uncertain relations stay pending.

The scaffold exposes reviewed commands. A future semantic relation classifier must
produce these commands; it must not write SQL or choose owners itself.
"""

import asyncio

from .models import EvolutionInput, Scope


class LongTermMemory:
    def __init__(self, store):
        self.store = store

    async def evolve(self, scope: Scope, memory_id: str, command: EvolutionInput):
        return await asyncio.to_thread(self.store.evolve, scope, memory_id, command)

    async def maintain(self, scope: Scope):
        groups = await asyncio.to_thread(self.store.rebuild_groups, scope)
        return {
            "status": "ok",
            "group_count": len(groups),
            "groups": groups,
            "mode": "reference_group",
            "semantic_synthesis": "not_implemented",
        }
