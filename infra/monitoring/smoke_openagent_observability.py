from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from openagent.harness.runtime import ModelProviderExchange, ModelTurnRequest, ModelTurnResponse
from openagent.local import create_file_runtime
from openagent.object_model import ToolResult
from openagent.tools import ToolCall


@dataclass(slots=True)
class ToolThenReplyExchangeModel:
    calls: int = 0

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        if request.messages[-1]["role"] == "user":
            return ModelProviderExchange(
                response=ModelTurnResponse(
                    tool_calls=[ToolCall(tool_name="echo", arguments={"text": "hello"})],
                    usage={
                        "input_tokens": 5,
                        "output_tokens": 4,
                        "cache_creation_input_tokens": 2,
                        "cache_read_input_tokens": 1,
                    },
                ),
                raw_response={"kind": "tool_call"},
                reasoning={"summary": "choose echo"},
            )
        return ModelProviderExchange(
            response=ModelTurnResponse(
                assistant_message="monitoring smoke complete",
                usage={"input_tokens": 3, "output_tokens": 6},
            ),
            raw_response={"kind": "assistant_message"},
            reasoning={"summary": "final answer"},
        )


@dataclass(slots=True)
class EchoTool:
    name: str = "echo"
    input_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})

    def description(self) -> str:
        return self.name

    def call(self, arguments: dict[str, object]) -> ToolResult:
        return ToolResult(tool_name=self.name, success=True, content=[str(arguments)])

    def check_permissions(self, arguments: dict[str, object]) -> str:
        del arguments
        return "allow"

    def is_concurrency_safe(self) -> bool:
        return True


def main() -> None:
    endpoint = os.getenv("OPENAGENT_OTLP_HTTP_ENDPOINT", "").strip()
    if not endpoint:
        raise RuntimeError("OPENAGENT_OTLP_HTTP_ENDPOINT is required")
    os.environ.setdefault("OPENAGENT_OTLP_SERVICE_NAME", "openagent-runtime-smoke")
    os.environ.setdefault("OPENAGENT_OBSERVABILITY_STDOUT", "false")

    with tempfile.TemporaryDirectory(prefix="openagent-runtime-smoke-") as tmp:
        root = Path(tmp) / ".openagent"
        runtime = create_file_runtime(
            model=ToolThenReplyExchangeModel(),
            session_root=str(root / "sessions"),
            tools=[EchoTool()],
            openagent_root=str(root),
        )
        runtime.run_turn("hello monitoring", "sess-monitoring-smoke")


if __name__ == "__main__":
    main()
