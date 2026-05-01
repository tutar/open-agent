"""HTTP search builtin tool."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

from openagent.object_model import JsonValue, ToolResult
from openagent.object_model.base import to_json_value
from openagent.tools.WebSearchTool.prompt import DESCRIPTION, WEB_SEARCH_TOOL_NAME
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_schema import object_schema, string_property
from openagent.tools.tool_web_env import default_web_search_backend
from openagent.tools.web import WebSearchBackend, WebSearchBackendError


class WebSearchTool(BuiltinToolBase):
    backend: WebSearchBackend

    def __init__(
        self,
        backend: WebSearchBackend | Callable[[str], list[dict[str, object]]] | None = None,
    ) -> None:
        super().__init__(
            name=WEB_SEARCH_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "query": string_property(
                        "Search query string to submit to the configured search backend.",
                        examples=["qwen3.6", "OpenAgent bootstrap prompts"],
                    )
                },
                required=["query"],
            ),
            aliases=["web_search"],
        )
        self.backend = default_web_search_backend(backend)

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        query = str(arguments["query"])
        try:
            results = self.backend.search(query)
        except WebSearchBackendError as exc:
            message = str(exc).strip() or "web search backend failed"
            return ToolResult(
                tool_name=self.name,
                success=False,
                content=[message],
                structured_content={
                    "ok": False,
                    "error": message,
                    "query": query,
                    "kind": "web_search_backend_error",
                    "results": [],
                },
            )
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
