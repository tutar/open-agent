"""Skill invocation builtin tool."""

from __future__ import annotations

from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.tools.SkillTool.prompt import DESCRIPTION, SKILL_TOOL_NAME
from openagent.tools.skills import SkillInvocationBridge
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_schema import object_schema, string_property


class SkillTool(BuiltinToolBase):
    bridge: SkillInvocationBridge

    def __init__(self, bridge: SkillInvocationBridge) -> None:
        super().__init__(
            name=SKILL_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "skill_id": string_property(
                        "Identifier of the skill to invoke.",
                        examples=["openai-docs", "imagegen"],
                    ),
                    "args": {
                        "type": "object",
                        "description": "Arguments passed to the skill.",
                        "additionalProperties": True,
                    },
                    "context": {
                        "type": "object",
                        "description": "Additional runtime context for the skill.",
                        "additionalProperties": True,
                    },
                },
                required=["skill_id"],
            ),
            aliases=["skill"],
        )
        self.bridge = bridge

    def call(self, arguments: dict[str, object]) -> ToolResult:
        skill_id = str(arguments["skill_id"])
        runtime_context = cast(dict[str, JsonValue], arguments.get("context", {}))
        if not isinstance(runtime_context, dict):
            runtime_context = {}
        args = cast(dict[str, JsonValue], arguments.get("args", {}))
        if not isinstance(args, dict):
            args = {}
        rendered = self.bridge.invoke_skill(skill_id, args=args, runtime_context=runtime_context)
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [rendered]),
            structured_content={"skill_id": skill_id},
        )
