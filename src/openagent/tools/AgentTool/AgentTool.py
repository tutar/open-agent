"""Delegation builtin tool."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from openagent.object_model import JsonObject, JsonValue, ToolResult
from openagent.object_model.base import to_json_value
from openagent.tools.AgentTool.prompt import AGENT_TOOL_NAME, DESCRIPTION
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_schema import object_schema, string_property


class AgentTool(BuiltinToolBase):
    handler: Callable[[dict[str, object], ToolExecutionContext | None], JsonObject]

    def __init__(
        self,
        handler: Callable[[dict[str, object], ToolExecutionContext | None], JsonObject],
    ) -> None:
        super().__init__(
            name=AGENT_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "prompt": string_property(
                        "Alternate prompt field for delegated work.",
                        examples=["Summarize the current session state"],
                    ),
                    "run_in_background": {
                        "type": "boolean",
                        "description": "When true, detach execution into a background delegated task.",
                    },
                    "agent_type": string_property(
                        "Optional delegated worker type label.",
                        examples=["delegate", "reviewer"],
                    ),
                    "task": string_property(
                        "Task description to delegate to the sub-agent.",
                        examples=["Review the current diff for regressions"],
                    ),
                },
                required=[],
            ),
            aliases=["agent"],
        )
        self.handler = handler

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        outcome = self.handler(arguments, context)
        structured_outcome = cast(JsonObject, to_json_value(outcome))
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [json.dumps(outcome, ensure_ascii=False)]),
            structured_content={"agent_linkage": structured_outcome},
        )
