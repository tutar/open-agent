"""Dreaming phase engine."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from openagent.durable_memory.dreaming.markdown import DreamingMarkdownWriter
from openagent.durable_memory.dreaming.models import (
    DreamingConfig,
    DreamingPhase,
    DreamingPhaseResult,
    DreamingSweepResult,
    PromotionCandidate,
    ShortTermRecallEntry,
)
from openagent.durable_memory.dreaming.state import DreamingStateStore
from openagent.durable_memory.models import (
    DurableWritePath,
    MemoryOverlay,
    MemoryPayloadType,
    MemoryRecord,
)
from openagent.object_model import JsonObject
from openagent.session.models import SessionMessage


class DreamingEngine:
    """Run Light, REM, and Deep memory consolidation phases."""

    def __init__(
        self,
        memory_root: str | Path,
        config: DreamingConfig | None = None,
    ) -> None:
        self.memory_root = Path(memory_root)
        self.config = config or DreamingConfig(enabled=True)
        self.state = DreamingStateStore(self.memory_root)
        self.markdown = DreamingMarkdownWriter(self.memory_root)

    def run_sweep(
        self,
        session_id: str,
        transcript_slice: list[SessionMessage],
        existing_records: list[MemoryRecord],
        agent_id: str | None = None,
    ) -> DreamingSweepResult:
        day = datetime.now(UTC).date().isoformat()
        result = DreamingSweepResult(session_id=session_id)
        with self.state.acquire_lock():
            ingested_entries = self._ingest_session_transcript(
                session_id,
                transcript_slice,
            )
            ingested_entries.extend(self._ingest_daily_memory(day))
            if ingested_entries:
                self.state.upsert_short_term_entries(ingested_entries)
            entries = self.state.read_short_term_entries()
            light_result = self._run_light(entries, day)
            rem_result = self._run_rem(entries, day)
            deep_result, promoted, skipped = self._run_deep(
                entries,
                existing_records,
                day,
                session_id,
                agent_id,
            )
            result.phase_results.extend([light_result, rem_result, deep_result])
            result.promoted_records.extend(promoted)
            result.skipped_refs.extend(skipped)
            if self.config.write_markdown:
                for phase_result in result.phase_results:
                    self.markdown.write_phase_report(phase_result, day)
                if self.config.write_memory_markdown:
                    self.markdown.append_memory_promotions(deep_result.candidates, day)
            if self.config.dream_diary_enabled:
                narrative = self._build_diary_narrative(rem_result)
                if narrative:
                    self.markdown.append_dream_diary(DreamingPhase.REM, narrative, day)
                    result.diary_entries.append(narrative)
        return result

    def _run_light(
        self,
        entries: list[ShortTermRecallEntry],
        day: str,
    ) -> DreamingPhaseResult:
        limited = entries[: self.config.max_candidates]
        self.state.record_phase_signals(DreamingPhase.LIGHT, [entry.key for entry in limited], day)
        return DreamingPhaseResult(
            phase=DreamingPhase.LIGHT,
            entries_seen=len(entries),
            themes=self._extract_themes([entry.text for entry in limited]),
            report_lines=[f"- {entry.text}" for entry in limited[:5]],
        )

    def _run_rem(
        self,
        entries: list[ShortTermRecallEntry],
        day: str,
    ) -> DreamingPhaseResult:
        limited = sorted(entries, key=lambda entry: entry.average_relevance, reverse=True)[
            : self.config.max_candidates
        ]
        self.state.record_phase_signals(DreamingPhase.REM, [entry.key for entry in limited], day)
        themes = self._extract_themes([entry.text for entry in limited])
        return DreamingPhaseResult(
            phase=DreamingPhase.REM,
            entries_seen=len(entries),
            themes=themes,
            report_lines=[f"- Reflection: {theme}" for theme in themes[:5]],
        )

    def _run_deep(
        self,
        entries: list[ShortTermRecallEntry],
        existing_records: list[MemoryRecord],
        day: str,
        session_id: str,
        agent_id: str | None,
    ) -> tuple[DreamingPhaseResult, list[MemoryRecord], list[str]]:
        signals = self.state.read_phase_signals()
        candidates = [
            self._score_candidate(entry, signals.get(entry.key, {}), day) for entry in entries
        ]
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        selected = candidates[: self.config.max_candidates]
        promoted: list[MemoryRecord] = []
        skipped_refs: list[str] = []
        for candidate in selected:
            if not candidate.eligible:
                continue
            if not self._source_is_grounded(candidate.entry):
                candidate.eligible = False
                candidate.reason = "source_not_grounded"
                continue
            duplicate = self._find_duplicate(candidate.entry.text, existing_records)
            if duplicate is not None:
                candidate.eligible = False
                candidate.reason = "duplicate"
                skipped_refs.append(duplicate.memory_id)
                continue
            promoted.append(self._record_from_candidate(candidate, session_id, agent_id))
        return (
            DreamingPhaseResult(
                phase=DreamingPhase.DEEP,
                entries_seen=len(entries),
                candidates=selected,
                themes=self._extract_themes([candidate.entry.text for candidate in selected]),
                report_lines=[f"- Promoted: {record.title}" for record in promoted],
            ),
            promoted,
            skipped_refs,
        )

    def _score_candidate(
        self,
        entry: ShortTermRecallEntry,
        raw_phase_signal: object,
        day: str,
    ) -> PromotionCandidate:
        phase_signal = raw_phase_signal if isinstance(raw_phase_signal, dict) else {}
        phase_boost = min(
            0.10,
            sum(
                int(value.get("count", 0)) * 0.03
                for value in phase_signal.values()
                if isinstance(value, dict)
            ),
        )
        weights = self.config.weights
        components = {
            "frequency": min(1.0, entry.recall_count / 5),
            "relevance": min(1.0, entry.average_relevance),
            "query_diversity": min(1.0, entry.unique_query_count / 3),
            "recency": 1.0 if day in entry.recall_days else 0.5,
            "consolidation": min(1.0, len(set(entry.recall_days)) / 3),
            "conceptual_richness": min(1.0, len(entry.concept_tags) / 6),
        }
        score = (
            components["frequency"] * weights.frequency
            + components["relevance"] * weights.relevance
            + components["query_diversity"] * weights.query_diversity
            + components["recency"] * weights.recency
            + components["consolidation"] * weights.consolidation
            + components["conceptual_richness"] * weights.conceptual_richness
            + phase_boost
        )
        reason = None
        eligible = True
        if score < self.config.min_score:
            eligible = False
            reason = "score_below_threshold"
        elif entry.recall_count < self.config.min_recall_count:
            eligible = False
            reason = "recall_count_below_threshold"
        elif entry.unique_query_count < self.config.min_unique_queries:
            eligible = False
            reason = "unique_queries_below_threshold"
        return PromotionCandidate(
            entry=entry,
            score=score,
            components=components,
            phase_boost=phase_boost,
            eligible=eligible,
            reason=reason,
        )

    def _ingest_session_transcript(
        self,
        session_id: str,
        transcript_slice: list[SessionMessage],
    ) -> list[ShortTermRecallEntry]:
        entries: list[ShortTermRecallEntry] = []
        for message in transcript_slice:
            if message.role not in {"user", "assistant"}:
                continue
            text = self._sanitize_text(message.content)
            if not text or self._is_low_signal(text):
                continue
            entries.append(
                ShortTermRecallEntry.from_text(
                    text=text,
                    source=f"session:{session_id}",
                    query=text[:80],
                    session_id=session_id,
                    source_type="session",
                )
            )
        return entries

    def _ingest_daily_memory(self, day: str) -> list[ShortTermRecallEntry]:
        memory_dir = self.memory_root / "memory"
        if not memory_dir.exists():
            return []
        entries: list[ShortTermRecallEntry] = []
        for path in sorted(memory_dir.glob("*.md")):
            if path.name in {"DREAMS.md", "MEMORY.md"}:
                continue
            text = path.read_text(encoding="utf-8")
            for index, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip("- ").strip()
                if not stripped or stripped.startswith("#"):
                    continue
                entries.append(
                    ShortTermRecallEntry.from_text(
                        text=stripped,
                        source=f"daily:{path.name}",
                        query=stripped[:80],
                        source_type="daily",
                        start_line=index,
                    )
                )
        if entries:
            self.state.write_checkpoint("daily-ingestion", {"last_day": day})
        return entries

    def _record_from_candidate(
        self,
        candidate: PromotionCandidate,
        session_id: str,
        agent_id: str | None,
    ) -> MemoryRecord:
        now = datetime.now(UTC).isoformat()
        title = candidate.entry.text[:80]
        return MemoryRecord(
            memory_id=f"dream_{candidate.entry.key}",
            scope=MemoryOverlay.PROJECT,
            type=MemoryPayloadType.NOTE,
            title=title,
            content=candidate.entry.text,
            summary=title,
            source=candidate.entry.source,
            created_at=now,
            updated_at=now,
            freshness="fresh",
            session_id=session_id,
            agent_id=agent_id,
            metadata={
                "write_path": DurableWritePath.DREAM.value,
                "dreaming_score": candidate.score,
                "dreaming_components": cast(JsonObject, candidate.components),
                "dreaming_phase_boost": candidate.phase_boost,
                "promotion_source": "dreaming",
            },
        )

    def _find_duplicate(
        self,
        text: str,
        existing_records: list[MemoryRecord],
    ) -> MemoryRecord | None:
        normalized = self._normalize(text)
        for record in existing_records:
            content_matches = self._normalize(record.content) == normalized
            title_matches = self._normalize(record.title) in normalized
            if content_matches or title_matches:
                return record
        return None

    def _source_is_grounded(self, entry: ShortTermRecallEntry) -> bool:
        if entry.source_type != "daily" or not entry.source.startswith("daily:"):
            return True
        daily_name = entry.source.split(":", 1)[1]
        path = self.memory_root / "memory" / daily_name
        if not path.exists():
            return False
        text = path.read_text(encoding="utf-8")
        return entry.text in text

    def _build_diary_narrative(self, rem_result: DreamingPhaseResult) -> str | None:
        if not rem_result.themes:
            return None
        themes = ", ".join(rem_result.themes[:3])
        return f"The agent noticed recurring themes around {themes}."

    def _extract_themes(self, texts: list[str]) -> list[str]:
        words = Counter(
            word
            for text in texts
            for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", text.lower())
            if word not in {"that", "this", "with", "from", "remember", "memory"}
        )
        return [word for word, _count in words.most_common(8)]

    def _sanitize_text(self, text: str) -> str:
        if not self.config.sanitize_session_corpus:
            return text.strip()
        text = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[redacted-email]", text)
        text = re.sub(r"\b(?:\d[ -]?){13,16}\b", "[redacted-number]", text)
        return text.strip()

    def _is_low_signal(self, text: str) -> bool:
        lowered = text.lower()
        excluded = ("def ", "class ", "stack trace", "build log", "git commit")
        return len(text.strip()) < 8 or any(pattern in lowered for pattern in excluded)

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())
