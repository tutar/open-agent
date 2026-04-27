# Runtime

当前 runtime 是本地、同进程、低复杂度实现，主代码位于 `harness/runtime/`。

## 当前支持

- `SimpleHarness` facade over an explicit `RalphLoop` turn runtime
- `core / io / projection / post_turn / hooks` 的运行时子结构
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
- real provider-backed assistant token streaming when the model adapter implements `stream_generate(...)`
- turn-level cancellation baseline
- timeout baseline
- retry baseline
- post-turn memory / continuity maintenance baseline
- runtime observability projection baseline
- no-op hook runtime baseline

## 当前不支持

- tool-aware cancellation recovery
- full retry policy customization
- full timeout semantics for every streaming backend
- 完整用户级 hook 扩展面
- 更细粒度的 runtime projection policy customization
