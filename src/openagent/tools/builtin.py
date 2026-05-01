"""Builtin tool facade and default assembly helpers."""

from __future__ import annotations

from collections.abc import Callable

from openagent.object_model import JsonObject
from openagent.tools.AgentTool import AgentTool
from openagent.tools.AskUserQuestionTool import AskUserQuestionTool
from openagent.tools.BashTool import BashTool
from openagent.tools.commands import (
    Command,
    CommandKind,
    CommandVisibility,
    ReviewCommand,
    ReviewCommandKind,
)
from openagent.tools.FileEditTool import FileEditTool
from openagent.tools.FileReadTool import FileReadTool
from openagent.tools.FileWriteTool import FileWriteTool
from openagent.tools.GlobTool import GlobTool
from openagent.tools.GrepTool import GrepTool
from openagent.tools.SkillTool import SkillTool
from openagent.tools.WebFetchTool import WebFetchTool
from openagent.tools.WebSearchTool import WebSearchTool
from openagent.tools.models import ToolExecutionContext
from openagent.tools.skills import SkillInvocationBridge
from openagent.tools.web import WebFetchBackend, WebSearchBackend

# Backward-compatible aliases kept on the historical module surface.
ReadTool = FileReadTool
WriteTool = FileWriteTool
EditTool = FileEditTool


def create_builtin_toolset(
    *,
    root: str = ".",
    web_fetch_backend: WebFetchBackend | None = None,
    web_search_backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None = None,
    agent_handler: (
        Callable[[dict[str, object], ToolExecutionContext | None], JsonObject] | None
    ) = None,
    skill_bridge: SkillInvocationBridge | None = None,
) -> list[object]:
    tools: list[object] = [
        FileReadTool(root),
        FileWriteTool(root),
        FileEditTool(root),
        GlobTool(root),
        GrepTool(root),
        BashTool(root),
        WebFetchTool(web_fetch_backend),
        WebSearchTool(web_search_backend),
        AskUserQuestionTool(),
    ]
    if agent_handler is not None:
        tools.append(AgentTool(agent_handler))
    if skill_bridge is not None:
        tools.append(SkillTool(skill_bridge))
    return tools


def create_builtin_commands() -> list[Command]:
    return [
        ReviewCommand(
            id="cmd.review",
            name="review",
            kind=CommandKind.REVIEW,
            description="Run a verification or critique command.",
            visibility=CommandVisibility.BOTH,
            source="builtin_review",
            review_kind=ReviewCommandKind.VERIFICATION,
            execution_mode="task_runtime",
        )
    ]
