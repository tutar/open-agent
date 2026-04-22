"""Required builtin tool baseline for the local OpenAgent runtime."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import cast

from openagent.object_model import JsonObject, JsonValue, RequiresAction, ToolResult
from openagent.object_model.base import to_json_value
from openagent.tools.commands import Command, CommandKind, CommandVisibility
from openagent.tools.errors import RequiresActionError
from openagent.tools.models import PermissionDecision, ToolExecutionContext, ToolSource
from openagent.tools.skills import SkillInvocationBridge
from openagent.tools.web import (
    BraveConfig,
    BraveWebSearchBackend,
    CallableWebSearchBackend,
    DefaultWebFetchBackend,
    DefaultWebSearchBackend,
    FirecrawlConfig,
    FirecrawlWebFetchBackend,
    FirecrawlWebSearchBackend,
    TavilyConfig,
    TavilyWebSearchBackend,
    WebFetchBackend,
    WebSearchBackend,
)


def _string_property(description: str, *, examples: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "string",
        "description": description,
    }
    if examples:
        payload["examples"] = examples
    return payload


def _object_schema(
    properties: dict[str, dict[str, object]],
    *,
    required: list[str],
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


@dataclass(slots=True)
class _BuiltinTool:
    name: str
    description_text: str
    input_schema: dict[str, object]
    aliases: list[str] = field(default_factory=list)
    source: ToolSource = ToolSource.BUILTIN
    visibility: str = "both"
    max_result_size_chars: int = 16_000
    supports_result_persistence: bool = False

    def description(
        self,
        arguments: dict[str, object] | None = None,
        describe_context: dict[str, object] | None = None,
    ) -> str:
        del arguments, describe_context
        return self.description_text

    def is_enabled(self, context: ToolExecutionContext | None = None) -> bool:
        del context
        return True

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return False

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return False

    def check_permissions(
        self,
        arguments: dict[str, object],
        tool_use_context: ToolExecutionContext | None = None,
    ) -> str:
        del arguments, tool_use_context
        return PermissionDecision.ALLOW.value

    def map_result(self, result: ToolResult, tool_use_id: str | None) -> ToolResult:
        if tool_use_id is not None:
            metadata = dict(result.metadata or {})
            metadata["tool_use_id"] = tool_use_id
            result.metadata = metadata
        return result


class ReadTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Read",
            description_text="Read a single file from the local workspace.",
            input_schema=_object_schema(
                {
                    "path": _string_property(
                        "Path to the file, relative to the current workspace root.",
                        examples=["README.md", "src/openagent/tools/builtin.py"],
                    )
                },
                required=["path"],
            ),
            aliases=["read"],
            supports_result_persistence=True,
        )
        self.root = root

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        path = _resolve_path(self.root, str(arguments["path"]))
        content = path.read_text(encoding="utf-8")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [content]),
        )


class WriteTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Write",
            description_text="Create or overwrite a file in the local workspace.",
            input_schema=_object_schema(
                {
                    "path": _string_property(
                        (
                            "Path to the file to create or overwrite, relative to the "
                            "current workspace root."
                        ),
                        examples=["notes/todo.txt", "tmp/output.json"],
                    ),
                    "content": _string_property(
                        "Full file contents to write.",
                        examples=["hello world\n", '{"ok": true}\n'],
                    ),
                },
                required=["path", "content"],
            ),
            aliases=["write"],
        )
        self.root = root

    def call(self, arguments: dict[str, object]) -> ToolResult:
        path = _resolve_path(self.root, str(arguments["path"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(arguments.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [str(path)]),
        )


class EditTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Edit",
            description_text="Apply a targeted replace edit to an existing file.",
            input_schema=_object_schema(
                {
                    "path": _string_property(
                        "Path to the file to edit, relative to the current workspace root.",
                        examples=["src/openagent/tools/builtin.py"],
                    ),
                    "old": _string_property(
                        "Exact text to replace.",
                        examples=["old_value = 1"],
                    ),
                    "new": _string_property(
                        "Replacement text.",
                        examples=["old_value = 2"],
                    ),
                },
                required=["path", "old", "new"],
            ),
            aliases=["edit"],
        )
        self.root = root

    def call(self, arguments: dict[str, object]) -> ToolResult:
        path = _resolve_path(self.root, str(arguments["path"]))
        old = str(arguments.get("old", ""))
        new = str(arguments.get("new", ""))
        content = path.read_text(encoding="utf-8")
        if old not in content:
            raise ValueError("target text not found")
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [str(path)]),
        )


class GlobTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Glob",
            description_text="List files matching a glob pattern.",
            input_schema=_object_schema(
                {
                    "pattern": _string_property(
                        "Glob pattern to match files relative to the workspace root.",
                        examples=["*", "*.py", "src/**/*.py"],
                    )
                },
                required=["pattern"],
            ),
            aliases=["glob"],
            supports_result_persistence=True,
        )
        self.root = root

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        pattern = str(arguments["pattern"])
        root = Path(self.root)
        matches = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if fnmatch(str(path.relative_to(root)), pattern)
        )
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], matches),
        )


class GrepTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Grep",
            description_text="Search file contents by substring.",
            input_schema=_object_schema(
                {
                    "pattern": _string_property(
                        "Substring or simple pattern to search for inside workspace files.",
                        examples=["TODO", "OpenAgent", "validation_failed"],
                    )
                },
                required=["pattern"],
            ),
            aliases=["grep"],
            supports_result_persistence=True,
        )
        self.root = root

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        needle = str(arguments["pattern"])
        results: list[str] = []
        for path in Path(self.root).rglob("*"):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if needle in line:
                    results.append(f"{path.relative_to(self.root)}:{line_number}:{line}")
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], results),
        )


class BashTool(_BuiltinTool):
    root: str = "."

    def __init__(self, root: str = ".") -> None:
        super().__init__(
            name="Bash",
            description_text="Execute a local shell command.",
            input_schema=_object_schema(
                {
                    "command": _string_property(
                        "Full shell command to execute in the current workspace root.",
                        examples=["ls -la", "pwd", "pytest -q tests/test_tools_alignment.py"],
                    )
                },
                required=["command"],
            ),
            aliases=["bash"],
            max_result_size_chars=64_000,
            supports_result_persistence=True,
        )
        self.root = root

    def check_permissions(
        self,
        arguments: dict[str, object],
        tool_use_context: ToolExecutionContext | None = None,
    ) -> str:
        del arguments, tool_use_context
        return PermissionDecision.ASK.value

    def call(self, arguments: dict[str, object]) -> ToolResult:
        command = str(arguments["command"])
        completed = subprocess.run(
            command,
            cwd=self.root,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout if completed.stdout else completed.stderr
        if completed.returncode != 0:
            raise RuntimeError(
                output.strip() or f"command failed with exit code {completed.returncode}"
            )
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [output.rstrip()]),
        )


class WebFetchTool(_BuiltinTool):
    backend: WebFetchBackend

    def __init__(self, backend: WebFetchBackend | None = None) -> None:
        super().__init__(
            name="WebFetch",
            description_text="Fetch a concrete URL over HTTP(S).",
            input_schema=_object_schema(
                {
                    "url": _string_property(
                        "Fully qualified HTTP or HTTPS URL to fetch.",
                        examples=["https://example.com", "http://127.0.0.1:8001/health"],
                    )
                },
                required=["url"],
            ),
            aliases=["web_fetch"],
            supports_result_persistence=True,
        )
        self.backend = backend or DefaultWebFetchBackend()

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        url = str(arguments["url"])
        document = self.backend.fetch(url)
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[document.content],
            structured_content=document.to_dict(),
        )


class WebSearchTool(_BuiltinTool):
    backend: WebSearchBackend

    def __init__(
        self,
        backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None = None,
    ) -> None:
        super().__init__(
            name="WebSearch",
            description_text="Search the web and return a result list.",
            input_schema=_object_schema(
                {
                    "query": _string_property(
                        "Search query string to submit to the configured search backend.",
                        examples=["qwen3.6", "OpenAgent bootstrap prompts"],
                    )
                },
                required=["query"],
            ),
            aliases=["web_search"],
        )
        if backend is None:
            self.backend = DefaultWebSearchBackend()
        elif isinstance(backend, WebSearchBackend):
            self.backend = backend
        else:
            self.backend = CallableWebSearchBackend(backend)

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        query = str(arguments["query"])
        results = self.backend.search(query)
        structured_results: list[JsonValue] = [to_json_value(result) for result in results]
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(
                list[JsonValue],
                [json.dumps([result.to_dict() for result in results], ensure_ascii=False)],
            ),
            structured_content={"results": structured_results},
        )


class AgentTool(_BuiltinTool):
    def __init__(self, handler: Callable[[dict[str, object]], dict[str, object]]) -> None:
        super().__init__(
            name="Agent",
            description_text="Spawn or delegate work to a sub-agent.",
            input_schema=_object_schema(
                {
                    "task": _string_property(
                        "Task description to delegate to the sub-agent.",
                        examples=["Review the current diff for regressions"],
                    )
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
        del context
        outcome = self.handler(arguments)
        structured_outcome = cast(JsonObject, to_json_value(outcome))
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=cast(list[JsonValue], [json.dumps(outcome, ensure_ascii=False)]),
            structured_content={"agent_linkage": structured_outcome},
        )


class SkillTool(_BuiltinTool):
    def __init__(self, bridge: SkillInvocationBridge) -> None:
        super().__init__(
            name="Skill",
            description_text="Invoke a discovered skill through the skill command surface.",
            input_schema=_object_schema(
                {
                    "skill_id": _string_property(
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


class AskUserQuestionTool(_BuiltinTool):
    def __init__(self) -> None:
        super().__init__(
            name="AskUserQuestion",
            description_text=(
                "Ask the user a structured question and block until a reply is provided."
            ),
            input_schema=_object_schema(
                {
                    "question": _string_property(
                        "Structured question to ask the user.",
                        examples=["Which branch should I use?", "Approve this command?"],
                    ),
                    "request_id": _string_property(
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


def create_builtin_toolset(
    *,
    root: str = ".",
    web_fetch_backend: WebFetchBackend | None = None,
    web_search_backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None = None,
    agent_handler: Callable[[dict[str, object]], dict[str, object]] | None = None,
    skill_bridge: SkillInvocationBridge | None = None,
) -> list[_BuiltinTool]:
    resolved_fetch_backend = web_fetch_backend or _default_web_fetch_backend()
    resolved_search_backend = _default_web_search_backend(web_search_backend)
    tools: list[_BuiltinTool] = [
        ReadTool(root),
        WriteTool(root),
        EditTool(root),
        GlobTool(root),
        GrepTool(root),
        BashTool(root),
        WebFetchTool(resolved_fetch_backend),
        WebSearchTool(resolved_search_backend),
        AskUserQuestionTool(),
    ]
    if agent_handler is not None:
        tools.append(AgentTool(agent_handler))
    if skill_bridge is not None:
        tools.append(SkillTool(skill_bridge))
    return tools


def create_builtin_commands() -> list[Command]:
    return [
        Command(
            id="cmd.review",
            name="review",
            kind=CommandKind.REVIEW,
            description="Run a verification or critique command.",
            visibility=CommandVisibility.BOTH,
            source="builtin_review",
        )
    ]


def _resolve_path(root: str, raw_path: str) -> Path:
    path = (Path(root) / raw_path).resolve()
    return path


def _default_web_fetch_backend() -> WebFetchBackend:
    backend_name = _env_value("OPENAGENT_WEBFETCH_BACKEND", "default").strip().lower()
    if backend_name in {"", "default"}:
        return DefaultWebFetchBackend()
    if backend_name == "firecrawl":
        return FirecrawlWebFetchBackend(_firecrawl_config_from_env())
    raise RuntimeError(f"Unsupported OPENAGENT_WEBFETCH_BACKEND: {backend_name}")


def _default_web_search_backend(
    backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None,
) -> WebSearchBackend:
    if backend is not None:
        if isinstance(backend, WebSearchBackend):
            return backend
        return CallableWebSearchBackend(backend)
    backend_name = _env_value("OPENAGENT_WEBSEARCH_BACKEND", "default").strip().lower()
    if backend_name in {"", "default"}:
        return DefaultWebSearchBackend()
    if backend_name == "firecrawl":
        return FirecrawlWebSearchBackend(_firecrawl_config_from_env())
    if backend_name == "tavily":
        return TavilyWebSearchBackend(_tavily_config_from_env())
    if backend_name == "brave":
        return BraveWebSearchBackend(_brave_config_from_env())
    raise RuntimeError(f"Unsupported OPENAGENT_WEBSEARCH_BACKEND: {backend_name}")


def _firecrawl_config_from_env() -> FirecrawlConfig:
    base_url = _env_value("OPENAGENT_FIRECRAWL_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError(
            "OPENAGENT_FIRECRAWL_BASE_URL is required when using the firecrawl web backend"
        )
    api_key = _env_value("OPENAGENT_FIRECRAWL_API_KEY")
    return FirecrawlConfig(
        base_url=base_url,
        api_key=api_key.strip() if isinstance(api_key, str) and api_key.strip() else None,
    )


def _tavily_config_from_env() -> TavilyConfig:
    api_key = _env_value("OPENAGENT_TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAGENT_TAVILY_API_KEY is required when using the tavily web search backend"
        )
    return TavilyConfig(
        api_key=api_key,
        base_url=_env_value("OPENAGENT_TAVILY_BASE_URL", "https://api.tavily.com").strip(),
        limit=_web_search_limit_from_env(),
    )


def _brave_config_from_env() -> BraveConfig:
    api_key = _env_value("OPENAGENT_BRAVE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAGENT_BRAVE_API_KEY is required when using the brave web search backend"
        )
    return BraveConfig(
        api_key=api_key,
        base_url=_env_value(
            "OPENAGENT_BRAVE_BASE_URL",
            "https://api.search.brave.com",
        ).strip(),
        limit=_web_search_limit_from_env(),
    )


def _web_search_limit_from_env() -> int:
    raw_limit = _env_value("OPENAGENT_WEBSEARCH_LIMIT", "5").strip()
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError("OPENAGENT_WEBSEARCH_LIMIT must be a positive integer") from exc
    if limit < 1:
        raise RuntimeError("OPENAGENT_WEBSEARCH_LIMIT must be a positive integer")
    return limit


def _env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value
    return _load_dotenv_values().get(name, default)


def _load_dotenv_values() -> dict[str, str]:
    dotenv_path = Path(".env")
    if not dotenv_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _strip_dotenv_value(raw_value.strip())
    return values


def _strip_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
