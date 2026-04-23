"""Context fragment providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openagent.harness.context_engineering.assembly.models import (
    AttachmentEnvelope,
    CapabilityExposure,
    EvidenceRef,
    StructuredContext,
)
from openagent.object_model import JsonObject


class ContextFragmentProvider(Protocol):
    def system_context(self) -> list[StructuredContext]: ...

    def user_context(self) -> list[StructuredContext]: ...

    def attachments(self) -> list[AttachmentEnvelope]: ...

    def capability_exposure(self) -> CapabilityExposure: ...

    def evidence_refs(self) -> list[EvidenceRef]: ...


@dataclass(slots=True)
class DefaultContextFragmentProvider:
    system_fragments: list[StructuredContext]
    user_fragments: list[StructuredContext]
    attachment_fragments: list[AttachmentEnvelope]
    capability_surface: CapabilityExposure
    evidence_fragments: list[EvidenceRef]

    def system_context(self) -> list[StructuredContext]:
        return self.system_fragments

    def user_context(self) -> list[StructuredContext]:
        return self.user_fragments

    def attachments(self) -> list[AttachmentEnvelope]:
        return self.attachment_fragments

    def capability_exposure(self) -> CapabilityExposure:
        return self.capability_surface

    def evidence_refs(self) -> list[EvidenceRef]:
        return self.evidence_fragments


def merge_capability_exposure(always_loaded: list[str]) -> JsonObject:
    return CapabilityExposure(always_loaded=always_loaded).to_dict()
