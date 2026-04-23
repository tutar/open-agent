"""Instruction-markdown models."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class InstructionRule(SerializableModel):
    source_path: str
    scope: str = "user"
    lifecycle: str = "startup"
    text: str = ""
    condition: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class InstructionDocument(SerializableModel):
    source_path: str
    rules: list[InstructionRule] = field(default_factory=list)
