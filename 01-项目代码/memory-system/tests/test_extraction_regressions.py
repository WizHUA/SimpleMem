"""Canonical field alignment, date semantics and one evidence repair deadline."""

import asyncio
import json
from datetime import timedelta

import pytest

from agent_memory.models import (
    Candidate,
    Event,
    Evidence,
    ExtractionWindow,
    ReferenceFact,
    Scope,
    Session,
    Turn,
    TurnInput,
    utcnow,
)
from agent_memory.providers import CallableModel, ModelOutputError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


def correction_window():
    return ExtractionWindow(
        session=Session(session_id="s", project_id="p"),
        new_turns=[
            Turn(
                turn_id="t",
                session_id="s",
                sequence=1,
                request_id="r",
                events=[
                    Event(
                        role="user",
                        content="正式更正：远航项目报告截止日期由2027年1月10日改为2027年2月3日，仅适用于远航项目。",
                    )
                ],
            )
        ],
        context_turns=[],
        reference_fields=[
            ReferenceFact(
                subject="远航项目报告", predicate="截止日期", value="2027-01-10", scope_type="project"
            )
        ],
    )


def correction_output(window, **changes):
    return {
        "summary": "报告截止日期已更正。",
        "candidates": [
            {
                "content": "远航项目报告截止日期改为2027年2月3日。",
                "subject": "远航项目报告",
                "predicate": "截止日期",
                "value": "2027-02-03",
                "scope_type": "project",
                "evidence": [
                    {"turn_id": "t", "event_index": 0, "quote": window.new_turns[0].events[0].content}
                ],
                **changes,
            }
        ],
    }


def test_real_failure_pattern_repairs_field_drift_and_event_date_without_rewriting_value():
    async def scenario():
        window = correction_window()
        invalid = correction_output(window, subject="远航项目报告截止日期", valid_from="2027-02-03T00:00:00Z")
        correct = correction_output(window)
        prompts = []

        async def chat(prompt):
            prompts.append(prompt)
            return json.dumps(invalid if len(prompts) == 1 else correct, ensure_ascii=False)

        result = await CallableModel(chat).extract(window)
        assert len(prompts) == 2
        assert "reference_fields" in prompts[0]
        assert "Subject repeats the canonical predicate" in prompts[1]
        assert "事件日期不等于事实生效日期" in prompts[1]
        candidate = result.candidates[0]
        assert (candidate.subject, candidate.predicate, candidate.value) == (
            "远航项目报告",
            "截止日期",
            "2027-02-03",
        )
        assert candidate.valid_from is None
        assert invalid["candidates"][0]["value"] == candidate.value

    asyncio.run(scenario())


def test_deadline_value_is_not_sufficient_evidence_for_fact_validity():
    async def scenario():
        window = correction_window()
        prompts = []

        async def chat(prompt):
            prompts.append(prompt)
            return json.dumps(correction_output(window, valid_from="2027-02-03T00:00:00Z"))

        with pytest.raises(ModelOutputError, match="explicit effective-time"):
            await CallableModel(chat).extract(window)
        assert len(prompts) == 2

    asyncio.run(scenario())


def test_explicit_effective_date_can_differ_from_deadline_value():
    async def scenario():
        window = correction_window()
        window.new_turns[0].events[0].content += "此项更正自2026年12月1日起生效。"
        model = CallableModel(
            lambda _: json.dumps(correction_output(window, valid_from="2026-12-01T00:00:00Z"))
        )
        candidate = (await model.extract(window)).candidates[0]
        assert candidate.valid_from.date().isoformat() == "2026-12-01"
        assert candidate.value == "2027-02-03"

    asyncio.run(scenario())


def test_schema_and_evidence_repair_share_two_total_calls_and_deadline():
    async def scenario():
        window = correction_window()
        calls = 0

        async def chat(prompt):
            nonlocal calls
            calls += 1
            return (
                "invalid JSON"
                if calls == 1
                else json.dumps(
                    correction_output(
                        window,
                        evidence=[{"turn_id": "directory-row", "event_index": 0, "quote": "2027-01-10"}],
                    )
                )
            )

        with pytest.raises(ModelOutputError, match="new turn"):
            await CallableModel(chat).extract(window)
        assert calls == 2
        calls = 0

        async def slow(prompt):
            nonlocal calls
            calls += 1
            if calls == 2:
                await asyncio.sleep(1)
            return json.dumps(correction_output(window, valid_from="2027-02-03T00:00:00Z"))

        with pytest.raises(TimeoutError):
            await CallableModel(slow, timeout=0.2).extract(window)
        assert calls == 2

    asyncio.run(scenario())


def test_reference_catalog_is_scoped_relevant_current_and_budgeted(tmp_path):
    async def scenario():
        runtime = MemoryRuntime(Settings(db_path=tmp_path / "memory.db"))
        scope = Scope(tenant_id="t", owner_id="alice")
        other = Scope(tenant_id="t", owner_id="bob")
        try:
            for principal, project, subject, valid_to in [
                (scope, "p", "远航项目报告", None),
                (scope, "q", "远航项目报告其它项目秘密", None),
                (other, "p", "远航项目报告他人秘密", None),
                (scope, "p", "远航项目报告过期事实", utcnow() - timedelta(days=1)),
                (scope, "p", "无关音乐", None),
            ]:
                s = runtime.store.create_session(principal, project_id=project)
                t = runtime.store.append_turn(
                    principal,
                    s.session_id,
                    TurnInput(request_id="one", events=[Event(role="user", content="日期值")]),
                )
                candidate = Candidate(
                    content=subject,
                    subject=subject,
                    predicate="截止日期" if subject != "无关音乐" else "风格",
                    value="2027-01-10",
                    scope_type="project",
                    durable=True,
                    valid_to=valid_to,
                    evidence=[Evidence(turn_id=t.turn_id, event_index=0, quote="日期值")],
                )
                memory = runtime.store.add_candidates(principal, s.session_id, [candidate])[0]
                from agent_memory.models import EvolutionInput

                runtime.store.evolve(
                    principal, memory.memory_id, EvolutionInput(action="promote", expected_version=1)
                )
            current = runtime.store.create_session(scope, project_id="p")
            runtime.store.append_turn(
                scope,
                current.session_id,
                TurnInput(
                    request_id="current",
                    events=[Event(role="user", content=correction_window().new_turns[0].events[0].content)],
                ),
            )
            window = await runtime.short_term.window(scope, current.session_id)
            assert [ref.subject for ref in window.reference_fields] == ["远航项目报告"]
            assert len(window.model_dump_json()) + 1000 <= runtime.settings.window_token_limit
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_unrepairable_extraction_preserves_watermark_and_empty_memory_store(tmp_path):
    async def scenario():
        calls = 0

        async def invalid(prompt):
            nonlocal calls
            calls += 1
            return json.dumps(
                {
                    "candidates": [
                        {
                            "content": "invented",
                            "subject": "entity",
                            "predicate": "field",
                            "value": "new",
                            "evidence": [{"turn_id": "fake", "event_index": 0, "quote": "invented"}],
                        }
                    ]
                }
            )

        runtime = MemoryRuntime(Settings(db_path=tmp_path / "memory.db"), model=CallableModel(invalid))
        scope = Scope(tenant_id="t", owner_id="o")
        try:
            session = runtime.store.create_session(scope)
            await runtime.append_turn(
                scope,
                session.session_id,
                TurnInput(request_id="one", events=[Event(role="user", content="报告日期调整")]),
            )
            with pytest.raises(ModelOutputError):
                await runtime.short_term.extract(scope, session.session_id)
            assert calls == 2
            assert runtime.store.get_session(scope, session.session_id).processed_sequence == 0
            assert runtime.store.memories(scope) == []
        finally:
            await runtime.close()

    asyncio.run(scenario())
