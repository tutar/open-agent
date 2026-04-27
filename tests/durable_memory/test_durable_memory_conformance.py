from dataclasses import dataclass
from pathlib import Path

from openagent.durable_memory import (
    AutoMemoryRuntimeConfig,
    DirectMemoryWriteRequest,
    DreamConsolidationRequest,
    DurableMemoryRecallRequest,
    DurableWritePath,
    FileMemoryStore,
    InMemoryMemoryStore,
    MemoryExtractionRequest,
    MemoryOverlay,
    MemoryPayloadType,
    MemoryRecord,
)
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.session import FileSessionStore, SessionMessage, SessionRecord
from openagent.tools import SimpleToolExecutor, StaticToolRegistry


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_conformance_memory_recall_and_consolidation(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    memory_store = FileMemoryStore(tmp_path / "memory")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="stored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )

    harness.run_turn("Remember that the launch code is sunrise", "case_memory")
    session = store.load_session("case_memory")
    consolidation = memory_store.consolidate("case_memory", session.messages)
    existing_records = memory_store.list()

    request = harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="What is the launch code?")],
        ),
        [],
    )

    restored_memory_store = FileMemoryStore(tmp_path / "memory")
    restored_harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="restored")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=restored_memory_store,
    )
    restored_recall = restored_memory_store.recall("case_memory", "launch code?")
    restored_request = restored_harness.build_model_input(
        SessionRecord(
            session_id="case_memory",
            messages=[SessionMessage(role="user", content="launch code?")],
        ),
        [],
    )

    assert consolidation.new_records or existing_records
    assert request.memory_context
    assert "sunrise" in str(request.memory_context[0]["content"])
    assert request.messages[-1] == {"role": "user", "content": "What is the launch code?"}
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
    assert restored_recall.recalled
    assert "sunrise" in str(restored_recall.recalled[0].content)
    if restored_request.memory_context:
        assert "sunrise" in str(restored_request.memory_context[0]["content"])


def test_conformance_durable_memory_index_and_bounded_recall(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    for index in range(8):
        memory_store.put(
            memory_store.extract(
                [SessionMessage(role="user", content=f"Remember launch detail {index}")],
                session_id=f"seed_{index}",
            )[0]
        )

    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )
    request = harness.build_model_input(
        SessionRecord(
            session_id="case_bounded_recall",
            messages=[SessionMessage(role="user", content="launch detail")],
        ),
        [],
    )

    assert 1 <= len(request.memory_context) <= 5
    assert request.messages[-1] == {"role": "user", "content": "launch detail"}
    assert [item["kind"] for item in request.startup_contexts] == ["session_start", "turn_zero"]
    assert all("Remember launch detail" in str(item["content"]) for item in request.memory_context)


def test_conformance_agent_global_long_memory(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    store = FileSessionStore(tmp_path / "sessions"),
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=store,
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
    )
    session_a = SessionRecord(
        session_id="session_a",
        agent_id="agent_shared",
        messages=[
            SessionMessage(role="user", content="Remember agent preference: codename atlas"),
            SessionMessage(role="assistant", content="Noted"),
        ],
    )
    memory_store.consolidate("session_a", session_a.messages, agent_id="agent_shared")

    request = harness.build_model_input(
        SessionRecord(
            session_id="session_b",
            agent_id="agent_shared",
            messages=[SessionMessage(role="user", content="What is the codename?")],
        ),
        [],
    )

    assert request.memory_context
    assert any("atlas" in str(item.get("content")) for item in request.memory_context)


def test_conformance_durable_memory_layering_and_surface_boundaries(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    direct = memory_store.direct_write(
        DirectMemoryWriteRequest(
            record=MemoryRecord(
                memory_id="mem_direct",
                scope=MemoryOverlay.PROJECT,
                type=MemoryPayloadType.PROJECT,
                title="Launch plan",
                content="Project launch plan and risks",
                summary="Launch plan",
                source="manual",
                created_at="2026-04-23T00:00:00Z",
                updated_at="2026-04-23T00:00:00Z",
            )
        )
    )
    extraction_job = memory_store.schedule(
        "sess_layering",
        [SessionMessage(role="user", content="Remember the team escalation workflow")],
    )
    extraction_result = memory_store.wait_for_job(extraction_job.job_id)
    dream_result = memory_store.dream(
        DreamConsolidationRequest.from_session_messages(
            session_id="sess_layering",
            transcript_slice=[SessionMessage(role="user", content="Project launch plan and risks")],
        )
    )
    handle = memory_store.prefetch_request(
        DurableMemoryRecallRequest(
            session_ref="sess_layering",
            query="launch plan",
            scope_selector=[MemoryOverlay.PROJECT, MemoryOverlay.TEAM],
            max_results=3,
        )
    )
    result = memory_store.collect(handle)

    assert direct.write_path is DurableWritePath.DIRECT_WRITE
    assert extraction_job.write_path is DurableWritePath.EXTRACT
    assert extraction_result.write_path is DurableWritePath.EXTRACT
    assert dream_result.write_path is DurableWritePath.DREAM
    assert handle.entrypoint_index is not None
    assert handle.entrypoint_index.entrypoints
    assert handle.manifest_entries
    assert result.recalled
    assert any(str(record.type) == MemoryPayloadType.PROJECT.value for record in result.recalled)


def test_conformance_durable_memory_taxonomy_vs_scope_axis(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    user_private = MemoryRecord(
        memory_id="mem_user_local",
        scope=MemoryOverlay.LOCAL,
        type=MemoryPayloadType.USER,
        title="Favorite editor",
        content="User prefers vim",
        summary="Prefers vim",
        source="manual",
        created_at="2026-04-23T00:00:00Z",
        updated_at="2026-04-23T00:00:00Z",
    )
    project_shared = MemoryRecord(
        memory_id="mem_project_team",
        scope=MemoryOverlay.TEAM,
        type=MemoryPayloadType.PROJECT,
        title="Migration plan",
        content="Project migration plan for the whole team",
        summary="Migration plan",
        source="manual",
        created_at="2026-04-23T00:00:00Z",
        updated_at="2026-04-23T00:00:00Z",
    )
    memory_store.direct_write(DirectMemoryWriteRequest(record=user_private))
    memory_store.direct_write(DirectMemoryWriteRequest(record=project_shared))

    records = {record.memory_id: record for record in memory_store.list()}

    assert records["mem_user_local"].type is MemoryPayloadType.USER
    assert records["mem_user_local"].scope is MemoryOverlay.LOCAL
    assert records["mem_project_team"].type is MemoryPayloadType.PROJECT
    assert records["mem_project_team"].scope is MemoryOverlay.TEAM


def test_conformance_durable_memory_overlay_family(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    for overlay in (
        MemoryOverlay.USER,
        MemoryOverlay.PROJECT,
        MemoryOverlay.TEAM,
        MemoryOverlay.AGENT,
        MemoryOverlay.LOCAL,
    ):
        memory_store.direct_write(
            DirectMemoryWriteRequest(
                record=MemoryRecord(
                    memory_id=f"mem_{overlay.value}",
                    scope=overlay,
                    type=MemoryPayloadType.NOTE,
                    title=f"{overlay.value} note",
                    content=f"{overlay.value} durable payload",
                    summary=f"{overlay.value} summary",
                    source="manual",
                    created_at="2026-04-23T00:00:00Z",
                    updated_at="2026-04-23T00:00:00Z",
                    agent_id="agent_x" if overlay is MemoryOverlay.AGENT else None,
                    session_id="sess_overlay" if overlay is MemoryOverlay.LOCAL else None,
                )
            )
        )

    handle = memory_store.prefetch_request(
        DurableMemoryRecallRequest(
            session_ref="sess_overlay",
            query="durable payload",
            scope_selector=[
                MemoryOverlay.USER,
                MemoryOverlay.PROJECT,
                MemoryOverlay.TEAM,
                MemoryOverlay.AGENT,
                MemoryOverlay.LOCAL,
            ],
            agent_id="agent_x",
        )
    )
    recalled_ids = {record.memory_id for record in memory_store.collect(handle).recalled}

    assert recalled_ids == {
        "mem_user",
        "mem_project",
        "mem_team",
        "mem_agent",
        "mem_local",
    }


def test_conformance_durable_memory_type_boundaries_and_exclusions() -> None:
    memory_store = InMemoryMemoryStore()
    extracted = memory_store.extract(
        [SessionMessage(role="user", content="I like concise commit messages")],
        session_id="sess_user",
    )
    excluded = memory_store.extract(
        [SessionMessage(role="user", content="def helper(): pass")],
        session_id="sess_code",
    )

    assert extracted
    assert extracted[0].type is MemoryPayloadType.USER
    assert excluded == []


def test_conformance_auto_memory_write_paths_and_consolidation(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    direct_result = memory_store.direct_write(
        DirectMemoryWriteRequest(
            record=MemoryRecord(
                memory_id="mem_direct_launch",
                scope=MemoryOverlay.PROJECT,
                type=MemoryPayloadType.PROJECT,
                title="Launch guide",
                content="Launch guide",
                summary="Launch guide",
                source="manual",
                created_at="2026-04-23T00:00:00Z",
                updated_at="2026-04-23T00:00:00Z",
            )
        )
    )
    extract_job = memory_store.schedule(
        "sess_paths",
        [SessionMessage(role="user", content="Launch guide")],
    )
    extract_result = memory_store.wait_for_job(extract_job.job_id)
    dream_result = memory_store.dream(
        DreamConsolidationRequest.from_session_messages(
            session_id="sess_paths",
            transcript_slice=[SessionMessage(role="user", content="Launch guide updated")],
        )
    )

    assert direct_result.write_path is DurableWritePath.DIRECT_WRITE
    assert extract_result.write_path is DurableWritePath.EXTRACT
    assert extract_result.skipped_refs == ["mem_direct_launch"]
    assert dream_result.write_path is DurableWritePath.DREAM


def test_conformance_memory_consolidation_background_safety(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    memory_store.direct_write(
        DirectMemoryWriteRequest(
            record=MemoryRecord(
                memory_id="mem_safe",
                scope=MemoryOverlay.PROJECT,
                type=MemoryPayloadType.PROJECT,
                title="Safe baseline",
                content="Existing durable memory",
                summary="Existing durable memory",
                source="manual",
                created_at="2026-04-23T00:00:00Z",
                updated_at="2026-04-23T00:00:00Z",
            )
        )
    )

    try:
        memory_store.dream(
            DreamConsolidationRequest.from_session_messages(
                session_id="sess_safe",
                transcript_slice=[SessionMessage(role="user", content="uncommitted change")],
                force_failure=True,
            )
        )
    except RuntimeError as exc:
        assert "dream consolidation failed" in str(exc)
    else:
        raise AssertionError("Expected dream consolidation failure")

    recalled = memory_store.recall("sess_safe", "safe baseline")
    assert [record.memory_id for record in recalled.recalled] == ["mem_safe"]


def test_auto_memory_runtime_gate_disables_recall_and_write(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(
        tmp_path / "memory",
        runtime_config=AutoMemoryRuntimeConfig(enabled=False),
    )

    assert memory_store.is_enabled() is False
    assert memory_store.recall("sess_disabled", "anything").recalled == []
    extract_result = memory_store.extract_memories(
        MemoryExtractionRequest.from_session_messages(
            session_id="sess_disabled",
            transcript_slice=[SessionMessage(role="user", content="Remember launch guide")],
        )
    )
    dream_result = memory_store.dream(
        DreamConsolidationRequest.from_session_messages(
            session_id="sess_disabled",
            transcript_slice=[SessionMessage(role="user", content="Remember launch guide")],
        )
    )

    assert extract_result.extracted == []
    assert dream_result.consolidated == []
    try:
        memory_store.direct_write(
            DirectMemoryWriteRequest(
                record=MemoryRecord(
                    memory_id="mem_disabled",
                    scope=MemoryOverlay.PROJECT,
                    type=MemoryPayloadType.PROJECT,
                    title="Disabled write",
                    content="Should not be written",
                    summary="Should not be written",
                    source="manual",
                    created_at="2026-04-23T00:00:00Z",
                    updated_at="2026-04-23T00:00:00Z",
                )
            )
        )
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("Expected direct write to be disabled")
