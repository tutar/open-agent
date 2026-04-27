from pathlib import Path

from openagent.durable_memory.dreaming import (
    DreamingMarkdownWriter,
    DreamingPhase,
    DreamingPhaseResult,
)


def test_markdown_writer_replaces_managed_phase_blocks_idempotently(tmp_path: Path) -> None:
    writer = DreamingMarkdownWriter(tmp_path)
    result = DreamingPhaseResult(
        phase=DreamingPhase.LIGHT,
        entries_seen=3,
        candidates=[],
        themes=["launch", "memory"],
        report_lines=["- Remember launch plan"],
    )

    writer.write_phase_report(result, day="2026-04-27")
    writer.write_phase_report(result, day="2026-04-27")
    daily_text = (tmp_path / "memory/dreaming/light/2026-04-27.md").read_text(encoding="utf-8")
    dreams_text = (tmp_path / "DREAMS.md").read_text(encoding="utf-8")

    assert daily_text.count("<!-- openagent:dreaming:light:start -->") == 1
    assert "## Light Sleep" in dreams_text
    assert dreams_text.count("Remember launch plan") == 1


def test_dream_diary_append_is_separate_from_promotion_artifacts(tmp_path: Path) -> None:
    writer = DreamingMarkdownWriter(tmp_path)

    writer.append_dream_diary(
        phase=DreamingPhase.REM,
        narrative="The agent noticed repeated launch planning themes.",
        day="2026-04-27",
    )

    dreams_text = (tmp_path / "DREAMS.md").read_text(encoding="utf-8")
    assert "## Dream Diary" in dreams_text
    assert "not a promotion source" in dreams_text
