"""Stable builtin tool-name constants shared across tools and harness prompts."""

from openagent.tools.AgentTool.prompt import AGENT_TOOL_NAME
from openagent.tools.AskUserQuestionTool.prompt import ASK_USER_QUESTION_TOOL_NAME
from openagent.tools.BashTool.prompt import BASH_TOOL_NAME
from openagent.tools.FileEditTool.prompt import EDIT_TOOL_NAME
from openagent.tools.FileReadTool.prompt import READ_TOOL_NAME
from openagent.tools.FileWriteTool.prompt import WRITE_TOOL_NAME
from openagent.tools.GlobTool.prompt import GLOB_TOOL_NAME
from openagent.tools.GrepTool.prompt import GREP_TOOL_NAME
from openagent.tools.SkillTool.prompt import SKILL_TOOL_NAME
from openagent.tools.WebFetchTool.prompt import WEB_FETCH_TOOL_NAME
from openagent.tools.WebSearchTool.prompt import WEB_SEARCH_TOOL_NAME

BUILTIN_TOOL_NAMES = (
    READ_TOOL_NAME,
    WRITE_TOOL_NAME,
    EDIT_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
    BASH_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    ASK_USER_QUESTION_TOOL_NAME,
    AGENT_TOOL_NAME,
    SKILL_TOOL_NAME,
)

__all__ = [
    "AGENT_TOOL_NAME",
    "ASK_USER_QUESTION_TOOL_NAME",
    "BASH_TOOL_NAME",
    "BUILTIN_TOOL_NAMES",
    "EDIT_TOOL_NAME",
    "GLOB_TOOL_NAME",
    "GREP_TOOL_NAME",
    "READ_TOOL_NAME",
    "SKILL_TOOL_NAME",
    "WEB_FETCH_TOOL_NAME",
    "WEB_SEARCH_TOOL_NAME",
    "WRITE_TOOL_NAME",
]
