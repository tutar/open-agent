"""Structured user-question builtin tool."""

from __future__ import annotations

from typing import cast

from openagent.object_model import JsonObject, RequiresAction, ToolResult
from openagent.object_model.base import to_json_value
from openagent.tools.AskUserQuestionTool.prompt import (
    ASK_USER_QUESTION_TOOL_NAME,
    DESCRIPTION,
)
from openagent.tools.errors import RequiresActionError
from openagent.tools.models import ToolExecutionContext
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_schema import object_schema, string_property


class AskUserQuestionTool(BuiltinToolBase):
    def __init__(self) -> None:
        super().__init__(
            name=ASK_USER_QUESTION_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "question": string_property(
                        "Structured question to ask the user.",
                        examples=["Which branch should I use?", "Approve this command?"],
                    ),
                    "request_id": string_property(
                        "Optional stable identifier for correlating the reply.",
                        examples=["req_123"],
                    ),
                },
                required=["question"],
            ),
            aliases=["ask_user"],
        )

    def call(
        self,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        question = str(arguments["question"])
        raise RequiresActionError(
            requires_action=RequiresAction(
                action_type="ask_user_question",
                session_id=context.session_id,
                description=question,
                tool_name=self.name,
                input=cast(JsonObject, to_json_value(arguments)),
                request_id=str(arguments["request_id"])
                if isinstance(arguments.get("request_id"), str)
                else None,
            )
        )
