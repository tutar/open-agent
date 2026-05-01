"""HTTP fetch builtin tool."""

from __future__ import annotations

from openagent.object_model import ToolResult
from openagent.tools.WebFetchTool.prompt import DESCRIPTION, WEB_FETCH_TOOL_NAME
from openagent.tools.tool_base import BuiltinToolBase
from openagent.tools.tool_schema import object_schema, string_property
from openagent.tools.tool_web_env import default_web_fetch_backend
from openagent.tools.web import WebFetchBackend, WebFetchBackendError


class WebFetchTool(BuiltinToolBase):
    backend: WebFetchBackend

    def __init__(self, backend: WebFetchBackend | None = None) -> None:
        super().__init__(
            name=WEB_FETCH_TOOL_NAME,
            description_text=DESCRIPTION,
            input_schema=object_schema(
                {
                    "url": string_property(
                        "Fully qualified HTTP or HTTPS URL to fetch.",
                        examples=["https://example.com", "http://127.0.0.1:8001/health"],
                    )
                },
                required=["url"],
            ),
            aliases=["web_fetch"],
            supports_result_persistence=True,
        )
        self.backend = backend or default_web_fetch_backend()

    def is_read_only(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def is_concurrency_safe(self, arguments: dict[str, object]) -> bool:
        del arguments
        return True

    def call(self, arguments: dict[str, object]) -> ToolResult:
        url = str(arguments["url"])
        try:
            document = self.backend.fetch(url)
        except WebFetchBackendError as exc:
            message = str(exc).strip() or "web fetch backend failed"
            return ToolResult(
                tool_name=self.name,
                success=False,
                content=[message],
                structured_content={
                    "ok": False,
                    "error": message,
                    "url": url,
                    "kind": "web_fetch_backend_error",
                },
            )
        return ToolResult(
            tool_name=self.name,
            success=True,
            content=[document.content],
            structured_content=document.to_dict(),
        )
