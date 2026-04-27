from openagent.durable_memory.dreaming import (
    DreamingConfig,
    DreamingPhase,
    PromotionWeights,
    ShortTermRecallEntry,
)


def test_dreaming_config_defaults_to_disabled_and_openclaw_paths() -> None:
    config = DreamingConfig()

    assert config.enabled is False
    assert config.short_term_store_relative_path == "memory/.dreams/short-term-recall.json"
    assert config.phase_signal_relative_path == "memory/.dreams/phase-signals.json"
    assert config.frequency == "0 3 * * *"


def test_short_term_recall_entry_tracks_scoring_signals() -> None:
    entry = ShortTermRecallEntry.from_text(
        text="Launch memory should preserve grounded project decisions",
        source="session:sess_1",
        query="launch memory",
        session_id="sess_1",
    )
    entry.reinforce(query="project decisions", relevance=0.8, day="2026-04-27")

    assert entry.recall_count == 2
    assert entry.unique_query_count == 2
    assert entry.average_relevance > 0
    assert "project" in entry.concept_tags


def test_promotion_weights_are_normalized() -> None:
    weights = PromotionWeights()

    assert round(weights.total, 2) == 1.00
    assert DreamingPhase.LIGHT.value == "light"
