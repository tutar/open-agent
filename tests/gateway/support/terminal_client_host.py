from __future__ import annotations

from dataclasses import dataclass

from openagent.harness.runtime.io import (
    ModelProviderExchange,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.host import OpenAgentHost, OpenAgentHostConfig


@dataclass(slots=True)
class StubTerminalModel:
    provider_family: str = "test"

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        latest = request.messages[-1]
        content = str(latest.get("content", ""))
        return ModelProviderExchange(
            response=ModelTurnResponse(assistant_message=f"Echo: {content}")
        )


def main() -> None:
    host = OpenAgentHost(
        OpenAgentHostConfig.from_env(),
        model=StubTerminalModel(),
    )
    host.start()


if __name__ == "__main__":
    main()
