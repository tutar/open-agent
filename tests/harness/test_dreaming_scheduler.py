from dataclasses import dataclass
from pathlib import Path

from openagent.durable_memory import DurableWritePath, FileMemoryStore
from openagent.durable_memory.dreaming import DreamingConfig
from openagent.harness.runtime import ModelTurnRequest, ModelTurnResponse, SimpleHarness
from openagent.session import FileSessionStore
from openagent.tools import SimpleToolExecutor, StaticToolRegistry


@dataclass(slots=True)
class ScriptedModel:
    responses: list[ModelTurnResponse]

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        del request
        return self.responses.pop(0)


def test_harness_does_not_schedule_dreaming_when_config_disabled(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
        dreaming_config=DreamingConfig(enabled=False),
    )

    harness.run_turn("Remember project memory: disabled dream recall", "case_disabled")

    assert harness.last_dreaming_job_id is None


def test_harness_does_not_schedule_dreaming_within_min_interval(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    # min_interval_seconds=86400: first turn triggers (last_run_at is None),
    # second turn is blocked because elapsed << 86400s.
    harness = SimpleHarness(
        model=ScriptedModel(
            [
                ModelTurnResponse(assistant_message="ok"),
                ModelTurnResponse(assistant_message="ok"),
            ]
        ),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
        dreaming_config=DreamingConfig(
            enabled=True,
            min_score=0.1,
            min_recall_count=1,
            min_interval_seconds=86400,
        ),
    )

    harness.run_turn("Remember project memory: first dream turn", "case_interval")
    first_dream_job_id = harness.last_dreaming_job_id
    assert first_dream_job_id is not None

    harness.run_turn("Remember project memory: second dream turn", "case_interval")

    assert harness.last_dreaming_job_id == first_dream_job_id


def test_harness_schedules_dreaming_only_when_config_enabled(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")
    harness = SimpleHarness(
        model=ScriptedModel([ModelTurnResponse(assistant_message="ok")]),
        sessions=FileSessionStore(tmp_path / "sessions"),
        tools=StaticToolRegistry([]),
        executor=SimpleToolExecutor(StaticToolRegistry([])),
        memory_store=memory_store,
        dreaming_config=DreamingConfig(
            enabled=True,
            min_score=0.1,
            min_recall_count=1,
            min_interval_seconds=0,
        ),
    )

    harness.run_turn("Remember project memory: scheduled dream recall", "case_dreaming")
    assert harness.last_memory_consolidation_job_id is not None
    assert harness.last_dreaming_job_id is not None
    extract_job_id = harness.last_memory_consolidation_job_id
    assert memory_store.wait_for_job(extract_job_id).write_path is DurableWritePath.EXTRACT

    result = memory_store.wait_for_job(harness.last_dreaming_job_id)

    assert result.updated_records
