from pathlib import Path

from openagent.durable_memory import MemoryOverlay, MemoryPayloadType, MemoryRecord
from openagent.durable_memory.dreaming import (
    DreamingConfig,
    DreamingEngine,
    DreamingPhase,
    DreamingStateStore,
    ShortTermRecallEntry,
)
from openagent.session import SessionMessage


def test_dreaming_engine_runs_light_rem_deep_and_promotes_records(tmp_path: Path) -> None:
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )

    result = engine.run_sweep(
        session_id="sess_dream",
        transcript_slice=[
            SessionMessage(
                role="user",
                content="Remember project launch decision: ship staged rollout",
            ),
        ],
        existing_records=[],
        agent_id="agent_a",
    )

    assert [phase.phase for phase in result.phase_results] == [
        DreamingPhase.LIGHT,
        DreamingPhase.REM,
        DreamingPhase.DEEP,
    ]
    assert result.promoted_records
    assert result.promoted_records[0].metadata["write_path"] == "dream"
    assert result.promoted_records[0].agent_id == "agent_a"


def test_deep_phase_skips_duplicate_existing_memory(tmp_path: Path) -> None:
    existing = MemoryRecord(
        memory_id="mem_existing",
        scope=MemoryOverlay.PROJECT,
        type=MemoryPayloadType.PROJECT,
        title="Remember project launch decision",
        content="Remember project launch decision: ship staged rollout",
        summary="Remember project launch decision",
        source="manual",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )
    state = DreamingStateStore(tmp_path)
    state.upsert_short_term_entries(
        [
            ShortTermRecallEntry.from_text(
                text="Remember project launch decision: ship staged rollout",
                source="session:sess_dream",
                query="launch decision",
            )
        ]
    )
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )

    result = engine.run_sweep(
        session_id="sess_dream",
        transcript_slice=[],
        existing_records=[existing],
    )

    assert result.promoted_records == []
    assert result.skipped_refs == ["mem_existing"]


def test_duplicate_deep_candidate_is_not_written_to_memory_markdown(tmp_path: Path) -> None:
    existing = MemoryRecord(
        memory_id="mem_existing",
        scope=MemoryOverlay.PROJECT,
        type=MemoryPayloadType.PROJECT,
        title="Remember project launch decision",
        content="Remember project launch decision: ship staged rollout",
        summary="Remember project launch decision",
        source="manual",
        created_at="2026-04-27T00:00:00Z",
        updated_at="2026-04-27T00:00:00Z",
    )
    state = DreamingStateStore(tmp_path)
    state.upsert_short_term_entries(
        [
            ShortTermRecallEntry.from_text(
                text="Remember project launch decision: ship staged rollout",
                source="session:sess_dream",
                query="launch decision",
            )
        ]
    )
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )

    engine.run_sweep(
        session_id="sess_dream",
        transcript_slice=[],
        existing_records=[existing],
    )
    memory_path = tmp_path / "MEMORY.md"

    assert not memory_path.exists()


def test_low_signal_messages_are_not_ingested_into_short_term_store(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )

    engine.run_sweep(
        session_id="sess_low",
        transcript_slice=[
            SessionMessage(role="user", content="ok"),
            SessionMessage(role="user", content="yes"),
            SessionMessage(role="assistant", content="def foo(): pass"),
            SessionMessage(role="assistant", content="class Bar: ..."),
        ],
        existing_records=[],
    )

    entries = state.read_short_term_entries()
    assert entries == []


def test_pii_is_redacted_before_entering_short_term_store(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1, sanitize_session_corpus=True),
    )

    engine.run_sweep(
        session_id="sess_pii",
        transcript_slice=[
            SessionMessage(
                role="user",
                content="Contact admin at admin@example.com about the deployment rollout plan today",
            ),
        ],
        existing_records=[],
    )

    entries = state.read_short_term_entries()
    assert entries
    assert "admin@example.com" not in entries[0].text
    assert "[redacted-email]" in entries[0].text


def test_score_below_threshold_prevents_promotion(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    state.upsert_short_term_entries(
        [
            ShortTermRecallEntry.from_text(
                text="Remember the deployment rollback plan for production release",
                source="session:sess_score",
                query="rollback",
            )
        ]
    )
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.99, min_recall_count=1),
    )

    result = engine.run_sweep(
        session_id="sess_score",
        transcript_slice=[],
        existing_records=[],
    )

    assert result.promoted_records == []
    deep = next(r for r in result.phase_results if r.phase == DreamingPhase.DEEP)
    ineligible = [c for c in deep.candidates if not c.eligible]
    assert ineligible
    assert ineligible[0].reason == "score_below_threshold"


def test_phase_boost_accumulates_across_multiple_sweeps(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    entry = ShortTermRecallEntry.from_text(
        text="Remember the deployment rollback plan for critical systems",
        source="session:sess_boost",
        query="rollback plan",
    )
    state.upsert_short_term_entries([entry])

    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.99, min_recall_count=1),
    )
    engine.run_sweep(session_id="sess_boost", transcript_slice=[], existing_records=[])
    engine.run_sweep(session_id="sess_boost", transcript_slice=[], existing_records=[])

    engine_low = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.99, min_recall_count=1),
    )
    result = engine_low.run_sweep(session_id="sess_boost", transcript_slice=[], existing_records=[])

    deep = next(r for r in result.phase_results if r.phase == DreamingPhase.DEEP)
    assert deep.candidates
    assert deep.candidates[0].phase_boost > 0.0


def test_daily_candidate_deleted_before_deep_is_not_promoted(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    daily_path = memory_dir / "2026-04-27.md"
    daily_path.write_text("- Remember grounded daily decision\n", encoding="utf-8")
    engine = DreamingEngine(
        memory_root=tmp_path,
        config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )
    engine.run_sweep(session_id="sess_daily", transcript_slice=[], existing_records=[])
    daily_path.write_text("", encoding="utf-8")

    second = engine.run_sweep(session_id="sess_daily", transcript_slice=[], existing_records=[])

    assert second.promoted_records == []
