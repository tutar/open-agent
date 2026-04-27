"""Dreaming memory data models."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from openagent.durable_memory.models import MemoryRecord
from openagent.object_model import JsonObject, JsonValue, SerializableModel


class DreamingPhase(StrEnum):
    LIGHT = "light"
    REM = "rem"
    DEEP = "deep"


@dataclass(slots=True)
class PromotionWeights(SerializableModel):
    frequency: float = 0.24
    relevance: float = 0.30
    query_diversity: float = 0.15
    recency: float = 0.15
    consolidation: float = 0.10
    conceptual_richness: float = 0.06

    @property
    def total(self) -> float:
        return (
            self.frequency
            + self.relevance
            + self.query_diversity
            + self.recency
            + self.consolidation
            + self.conceptual_richness
        )


@dataclass(slots=True)
class DreamingConfig(SerializableModel):
    enabled: bool = False
    frequency: str = "0 3 * * *"
    min_interval_seconds: int = 86_400
    lookback_days: int = 7
    max_candidates: int = 20
    min_score: float = 0.45
    min_recall_count: int = 2
    min_unique_queries: int = 1
    write_markdown: bool = True
    write_memory_markdown: bool = True
    dream_diary_enabled: bool = True
    sanitize_session_corpus: bool = True
    short_term_store_relative_path: str = "memory/.dreams/short-term-recall.json"
    phase_signal_relative_path: str = "memory/.dreams/phase-signals.json"
    session_corpus_relative_path: str = "memory/.dreams/session-corpus"
    weights: PromotionWeights = field(default_factory=PromotionWeights)

    @classmethod
    def from_dict(cls, data: JsonObject) -> DreamingConfig:
        weights_data = data.get("weights", {})
        weights = (
            PromotionWeights.from_dict(weights_data)
            if isinstance(weights_data, dict)
            else PromotionWeights()
        )
        return cls(
            enabled=bool(data.get("enabled", False)),
            frequency=str(data.get("frequency", "0 3 * * *")),
            min_interval_seconds=_int_value(data.get("min_interval_seconds"), 86_400),
            lookback_days=_int_value(data.get("lookback_days"), 7),
            max_candidates=_int_value(data.get("max_candidates"), 20),
            min_score=_float_value(data.get("min_score"), 0.45),
            min_recall_count=_int_value(data.get("min_recall_count"), 2),
            min_unique_queries=_int_value(data.get("min_unique_queries"), 1),
            write_markdown=bool(data.get("write_markdown", True)),
            write_memory_markdown=bool(data.get("write_memory_markdown", True)),
            dream_diary_enabled=bool(data.get("dream_diary_enabled", True)),
            sanitize_session_corpus=bool(data.get("sanitize_session_corpus", True)),
            short_term_store_relative_path=str(
                data.get("short_term_store_relative_path", "memory/.dreams/short-term-recall.json")
            ),
            phase_signal_relative_path=str(
                data.get("phase_signal_relative_path", "memory/.dreams/phase-signals.json")
            ),
            session_corpus_relative_path=str(
                data.get("session_corpus_relative_path", "memory/.dreams/session-corpus")
            ),
            weights=weights,
        )


@dataclass(slots=True)
class ShortTermRecallEntry(SerializableModel):
    key: str
    text: str
    source: str
    source_type: str = "session"
    session_id: str | None = None
    start_line: int | None = None
    recall_count: int = 1
    daily_count: int = 0
    grounded_count: int = 1
    relevance_total: float = 1.0
    query_hashes: list[str] = field(default_factory=list)
    recall_days: list[str] = field(default_factory=list)
    concept_tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: JsonObject = field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        text: str,
        source: str,
        query: str,
        session_id: str | None = None,
        source_type: str = "session",
        start_line: int | None = None,
    ) -> ShortTermRecallEntry:
        normalized_text = _normalize_text(text)
        query_hash = _hash_text(query)
        now = datetime.now(UTC).isoformat()
        return cls(
            key=_hash_text(f"{source}:{normalized_text}"),
            text=text.strip(),
            source=source,
            source_type=source_type,
            session_id=session_id,
            start_line=start_line,
            query_hashes=[query_hash],
            recall_days=[now[:10]],
            concept_tags=_extract_concept_tags(text),
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, data: JsonObject) -> ShortTermRecallEntry:
        return cls(
            key=str(data["key"]),
            text=str(data["text"]),
            source=str(data["source"]),
            source_type=str(data.get("source_type", "session")),
            session_id=cast(str | None, data.get("session_id")),
            start_line=_optional_int_value(data.get("start_line")),
            recall_count=_int_value(data.get("recall_count"), 1),
            daily_count=_int_value(data.get("daily_count"), 0),
            grounded_count=_int_value(data.get("grounded_count"), 1),
            relevance_total=_float_value(data.get("relevance_total"), 1.0),
            query_hashes=_str_list(data.get("query_hashes")),
            recall_days=_str_list(data.get("recall_days")),
            concept_tags=_str_list(data.get("concept_tags")),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
            updated_at=str(data.get("updated_at", datetime.now(UTC).isoformat())),
            metadata=cast(JsonObject, data.get("metadata", {})),
        )

    @property
    def unique_query_count(self) -> int:
        return len(set(self.query_hashes))

    @property
    def average_relevance(self) -> float:
        if self.recall_count <= 0:
            return 0.0
        return self.relevance_total / self.recall_count

    def reinforce(self, query: str, relevance: float = 1.0, day: str | None = None) -> None:
        self.recall_count += 1
        self.relevance_total += relevance
        query_hash = _hash_text(query)
        if query_hash not in self.query_hashes:
            self.query_hashes.append(query_hash)
        recall_day = day or datetime.now(UTC).date().isoformat()
        if recall_day not in self.recall_days:
            self.recall_days.append(recall_day)
        self.concept_tags = sorted(set([*self.concept_tags, *_extract_concept_tags(query)]))
        self.updated_at = datetime.now(UTC).isoformat()


@dataclass(slots=True)
class PromotionCandidate(SerializableModel):
    entry: ShortTermRecallEntry
    score: float
    components: dict[str, float] = field(default_factory=dict)
    phase_boost: float = 0.0
    eligible: bool = True
    reason: str | None = None

    @classmethod
    def from_dict(cls, data: JsonObject) -> PromotionCandidate:
        entry_data = data["entry"]
        if not isinstance(entry_data, dict):
            raise TypeError("PromotionCandidate.entry must be an object")
        return cls(
            entry=ShortTermRecallEntry.from_dict(entry_data),
            score=_float_value(data.get("score"), 0.0),
            components=_float_dict(data.get("components")),
            phase_boost=_float_value(data.get("phase_boost"), 0.0),
            eligible=bool(data.get("eligible", True)),
            reason=str(data["reason"]) if data.get("reason") is not None else None,
        )


@dataclass(slots=True)
class DreamingPhaseResult(SerializableModel):
    phase: DreamingPhase
    entries_seen: int = 0
    candidates: list[PromotionCandidate] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    report_lines: list[str] = field(default_factory=list)
    artifact_path: str | None = None


@dataclass(slots=True)
class DreamingSweepResult(SerializableModel):
    session_id: str
    phase_results: list[DreamingPhaseResult] = field(default_factory=list)
    promoted_records: list[MemoryRecord] = field(default_factory=list)
    skipped_refs: list[str] = field(default_factory=list)
    diary_entries: list[str] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _int_value(value: JsonValue | None, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    return default


def _optional_int_value(value: JsonValue | None) -> int | None:
    if value is None:
        return None
    return _int_value(value, 0)


def _float_value(value: JsonValue | None, default: float) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        return float(value)
    return default


def _str_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _float_dict(value: JsonValue | None) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {key: _float_value(item, 0.0) for key, item in value.items()}


def _hash_text(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()[:16]


def _extract_concept_tags(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", text.lower())
    stop_words = {"that", "this", "with", "from", "should", "remember", "memory"}
    return sorted({word for word in words if word not in stop_words})[:12]
