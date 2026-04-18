# Runtime

当前 runtime 是本地、同进程、低复杂度实现。

## 当前支持

- `SimpleHarness` facade over an explicit `RalphLoop` turn runtime
- `run_turn_stream(...)`
- `turn_started`
- `assistant_delta`
- `assistant_message`
- `tool_started`
- `tool_progress`
- `tool_failed`
- `tool_cancelled`
- `tool_result`
- `requires_action`
- `turn_completed`
- `turn_failed`
- 审批后的 `continue_turn(...)`
- single-shot `generate(...)`
- streaming `stream_generate(...)`
- turn-level cancellation baseline
- timeout baseline
- retry baseline

## 当前不支持

- model token streaming
- tool-aware cancellation recovery
- full retry policy customization
- full timeout semantics for every streaming backend

