"""Context assembly pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.context_engineering.assembly.attachments import (
    assemble_attachment_stream,
)
from openagent.harness.context_engineering.assembly.models import (
    ContextAssemblyInput,
    ContextAssemblyResult,
)
from openagent.object_model import JsonObject, JsonValue


def _payload_content(item: JsonObject) -> str:
    payload = item.get("payload")
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    return str(content) if content is not None else ""


def _string_field(item: JsonObject, key: str) -> str | None:
    value = item.get(key)
    return str(value) if value is not None else None


def _user_context_message(item: JsonObject) -> JsonObject:
    return {
        "role": "user",
        "content": _payload_content(item),
        "metadata": {
            "context_scope": _string_field(item, "scope"),
            "context_lifecycle": _string_field(item, "lifecycle"),
            "provenance": _string_field(item, "provenance"),
        },
    }


def _startup_context_fragment(item: JsonObject) -> str | None:
    kind = _string_field(item, "kind") or "startup"
    content = _payload_content(item).strip()
    if content:
        return f"Startup context ({kind}): {content}"
    return f"Startup context ({kind})."


@dataclass(slots=True)
class ContextAssemblyPipeline:
    def assemble(self, assembly_input: ContextAssemblyInput) -> ContextAssemblyResult:
        sections = assembly_input.bootstrap_prompt_sections
        static_section_text: list[str] = []
        dynamic_section_text: list[str] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            text = str(section.get("text", ""))
            if not text:
                continue
            if bool(section.get("dynamic")):
                dynamic_section_text.append(text)
            else:
                static_section_text.append(text)
        section_text = [*static_section_text, *dynamic_section_text]
        startup_fragments = [
            fragment
            for item in assembly_input.startup_contexts
            if isinstance(item, dict)
            for fragment in [_startup_context_fragment(item)]
            if fragment
        ]
        system_fragments = [
            _payload_content(item)
            for item in assembly_input.system_context
            if isinstance(item, dict) and _payload_content(item)
        ]
        system_prompt = (
            "\n\n".join([*section_text, *startup_fragments, *system_fragments]).strip() or None
        )
        user_context_messages = [
            _user_context_message(item)
            for item in assembly_input.user_context
            if isinstance(item, dict) and _payload_content(item)
        ]
        prompt_blocks: JsonObject = {
            "static_blocks": cast_json_list(static_section_text),
            "dynamic_blocks": cast_json_list(dynamic_section_text),
            "attribution_prefix": "OpenAgent bootstrap prompt",
        }
        return ContextAssemblyResult(
            system_prompt=system_prompt,
            message_stream=[*assembly_input.transcript, *user_context_messages],
            attachment_stream=assemble_attachment_stream(
                assembly_input.attachments,
                assembly_input.evidence_refs,
            ),
            capability_surface=assembly_input.capability_surface,
            evidence_refs=assembly_input.evidence_refs,
            request_metadata=assembly_input.request_metadata,
            prompt_sections=sections,
            prompt_blocks=prompt_blocks,
            system_context=assembly_input.system_context,
            user_context=assembly_input.user_context,
            startup_contexts=assembly_input.startup_contexts,
        )


def cast_json_list(items: list[str]) -> list[JsonValue]:
    return [item for item in items]
