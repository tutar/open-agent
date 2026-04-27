"""Attachment assembly helpers."""

from __future__ import annotations

from openagent.object_model import JsonObject


def assemble_attachment_stream(
    attachments: list[JsonObject],
    evidence_refs: list[JsonObject],
) -> list[JsonObject]:
    return [*attachments, *evidence_refs]
