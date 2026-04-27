import pytest
from pathlib import Path

from openagent.durable_memory.dreaming import (
    DreamingPhase,
    DreamingStateStore,
    ShortTermRecallEntry,
)


def test_state_store_persists_short_term_entries_and_phase_signals(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    entry = ShortTermRecallEntry.from_text(
        text="Remember the launch checklist",
        source="daily:2026-04-27",
        query="launch",
    )

    state.upsert_short_term_entries([entry])
    restored = DreamingStateStore(tmp_path)
    restored_entries = restored.read_short_term_entries()
    restored.record_phase_signals(DreamingPhase.LIGHT, [entry.key], day="2026-04-27")
    signals = restored.read_phase_signals()

    assert [item.key for item in restored_entries] == [entry.key]
    assert signals[entry.key]["light"]["count"] == 1
    assert signals[entry.key]["light"]["last_seen_day"] == "2026-04-27"
    assert (tmp_path / "memory/.dreams/short-term-recall.json").exists()


def test_state_store_lock_prevents_nested_dreaming_runs(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)

    with state.acquire_lock():
        assert state.is_locked()

    assert not state.is_locked()


def test_state_store_lock_raises_when_already_held(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)

    with state.acquire_lock():
        with pytest.raises(RuntimeError, match="locked"):
            with state.acquire_lock():
                pass


def test_upsert_short_term_entries_merges_recall_signals(tmp_path: Path) -> None:
    state = DreamingStateStore(tmp_path)
    entry = ShortTermRecallEntry.from_text(
        text="Remember the deployment rollback plan",
        source="session:sess_1",
        query="rollback plan",
    )
    state.upsert_short_term_entries([entry])

    second = ShortTermRecallEntry.from_text(
        text="Remember the deployment rollback plan",
        source="session:sess_1",
        query="deployment decision",
    )
    state.upsert_short_term_entries([second])

    merged = state.read_short_term_entries()
    assert len(merged) == 1
    assert merged[0].recall_count == 2
    assert merged[0].unique_query_count == 2
    assert merged[0].relevance_total >= 2.0
