from pathlib import Path

from openagent.durable_memory import (
    DreamConsolidationRequest,
    DurableWritePath,
    FileMemoryStore,
)
from openagent.durable_memory.dreaming import DreamingConfig
from openagent.session import SessionMessage


def test_file_memory_store_dream_uses_dreaming_engine_and_persists_artifacts(
    tmp_path: Path,
) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")

    result = memory_store.dream(
        DreamConsolidationRequest.from_session_messages(
            session_id="sess_dream_store",
            transcript_slice=[
                SessionMessage(
                    role="user",
                    content="Remember project launch decision: keep rollout reversible",
                )
            ],
            dreaming_config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
        )
    )

    assert result.write_path is DurableWritePath.DREAM
    assert result.consolidated
    assert memory_store.recall("sess_dream_store", "reversible rollout").recalled
    assert (tmp_path / "memory/.dreams/short-term-recall.json").exists()
    assert (tmp_path / "DREAMS.md").exists()


def test_memory_store_can_schedule_dream_job(tmp_path: Path) -> None:
    memory_store = FileMemoryStore(tmp_path / "memory")

    job = memory_store.schedule(
        "sess_dream_job",
        [SessionMessage(role="user", content="Remember project memory: nightly dream job")],
        write_path=DurableWritePath.DREAM,
        dreaming_config=DreamingConfig(enabled=True, min_score=0.1, min_recall_count=1),
    )
    result = memory_store.wait_for_job(job.job_id)

    assert job.write_path is DurableWritePath.DREAM
    assert result.write_path is DurableWritePath.DREAM
    assert result.updated_records
