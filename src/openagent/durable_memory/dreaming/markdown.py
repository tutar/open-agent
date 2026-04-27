"""Human-readable dreaming Markdown artifacts."""

from __future__ import annotations

from pathlib import Path

from openagent.durable_memory.dreaming.models import (
    DreamingPhase,
    DreamingPhaseResult,
    PromotionCandidate,
)


class DreamingMarkdownWriter:
    """Write OpenClaw-style dreaming reports with managed blocks."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write_phase_report(self, result: DreamingPhaseResult, day: str) -> Path:
        report = self._render_phase_report(result)
        phase_dir = self.root / "memory" / "dreaming" / result.phase.value
        phase_dir.mkdir(parents=True, exist_ok=True)
        phase_path = phase_dir / f"{day}.md"
        self._replace_managed_block(phase_path, result.phase, report)
        self._replace_managed_block(self.root / "DREAMS.md", result.phase, report)
        result.artifact_path = str(phase_path)
        return phase_path

    def append_dream_diary(self, phase: DreamingPhase, narrative: str, day: str) -> None:
        path = self.root / "DREAMS.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Dreams\n\n"
        if "## Dream Diary" not in existing:
            existing = existing.rstrip() + "\n\n## Dream Diary\n\n"
        entry = (
            f"### {day} {phase.value.upper()}\n\n"
            f"{narrative}\n\n"
            "_Dream Diary is not a promotion source._\n"
        )
        if narrative in existing:
            return
        path.write_text(existing.rstrip() + "\n\n" + entry, encoding="utf-8")

    def append_memory_promotions(self, candidates: list[PromotionCandidate], day: str) -> None:
        eligible_candidates = [candidate for candidate in candidates if candidate.eligible]
        if not eligible_candidates:
            return
        path = self.root / "MEMORY.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Memory\n\n"
        lines = [f"## Dream Promotions {day}", ""]
        for candidate in eligible_candidates:
            lines.append(f"- {candidate.entry.text}")
        block = "\n".join(lines).rstrip() + "\n"
        if block in existing:
            return
        path.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")

    def _replace_managed_block(self, path: Path, phase: DreamingPhase, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.exists() else "# Dreams\n\n"
        start = f"<!-- openagent:dreaming:{phase.value}:start -->"
        end = f"<!-- openagent:dreaming:{phase.value}:end -->"
        block = f"{start}\n{body.rstrip()}\n{end}"
        start_index = text.find(start)
        end_index = text.find(end)
        if start_index >= 0 and end_index >= start_index:
            end_index += len(end)
            text = text[:start_index].rstrip() + "\n\n" + block + text[end_index:]
        else:
            text = text.rstrip() + "\n\n" + block + "\n"
        path.write_text(text.lstrip(), encoding="utf-8")

    def _render_phase_report(self, result: DreamingPhaseResult) -> str:
        title = {
            DreamingPhase.LIGHT: "Light Sleep",
            DreamingPhase.REM: "REM Sleep",
            DreamingPhase.DEEP: "Deep Sleep",
        }[result.phase]
        lines = [f"## {title}", "", f"Entries seen: {result.entries_seen}"]
        if result.themes:
            lines.extend(["", "Themes:", *[f"- {theme}" for theme in result.themes]])
        if result.report_lines:
            lines.extend(["", *result.report_lines])
        if result.candidates:
            lines.extend(
                [
                    "",
                    "Candidates:",
                    *[
                        f"- {candidate.entry.text} (score: {candidate.score:.2f})"
                        for candidate in result.candidates
                    ],
                ]
            )
        return "\n".join(lines)
