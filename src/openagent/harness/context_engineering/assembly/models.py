"""Context assembly object model."""

from __future__ import annotations

from dataclasses import dataclass, field

from openagent.object_model import JsonObject, SerializableModel


@dataclass(slots=True)
class StructuredContext(SerializableModel):
    scope: str
    lifecycle: str
    payload: JsonObject
    provenance: str


@dataclass(slots=True)
class AttachmentEnvelope(SerializableModel):
    kind: str
    ref: str
    payload: JsonObject = field(default_factory=dict)
    audience: str = "model"
    scope: str = "thread"


@dataclass(slots=True)
class CapabilityExposure(SerializableModel):
    always_loaded: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    searchable: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceRef(SerializableModel):
    kind: str
    ref: str
    preview: str | None = None
    provenance: str = "runtime"


@dataclass(slots=True)
class ContextAssemblyInput(SerializableModel):
    transcript: list[JsonObject] = field(default_factory=list)
    bootstrap_prompt_sections: list[JsonObject] = field(default_factory=list)
    system_context: list[JsonObject] = field(default_factory=list)
    user_context: list[JsonObject] = field(default_factory=list)
    attachments: list[JsonObject] = field(default_factory=list)
    capability_surface: JsonObject = field(default_factory=dict)
    evidence_refs: list[JsonObject] = field(default_factory=list)
    request_metadata: JsonObject = field(default_factory=dict)
    startup_contexts: list[JsonObject] = field(default_factory=list)


@dataclass(slots=True)
class ContextAssemblyResult(SerializableModel):
    system_prompt: str | None = None
    message_stream: list[JsonObject] = field(default_factory=list)
    attachment_stream: list[JsonObject] = field(default_factory=list)
    capability_surface: JsonObject = field(default_factory=dict)
    evidence_refs: list[JsonObject] = field(default_factory=list)
    request_metadata: JsonObject = field(default_factory=dict)
    prompt_sections: list[JsonObject] = field(default_factory=list)
    prompt_blocks: JsonObject | None = None
    system_context: list[JsonObject] = field(default_factory=list)
    user_context: list[JsonObject] = field(default_factory=list)
    startup_contexts: list[JsonObject] = field(default_factory=list)
