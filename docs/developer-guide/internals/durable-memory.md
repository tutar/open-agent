# Durable Memory

`durable_memory/` 负责长期记忆的 recall、layered selection 与 consolidation。

这里的边界是：

- durable memory 不属于 `session`
- durable memory 不承担 transcript restore
- recalled memory 进入 context plane，而不是伪装成 transcript 消息
- transcript 和 short-term memory 都不是 durable memory 的替代品

当前本地 baseline 包含：

- `MemoryStore` protocol
- `InMemoryMemoryStore`
- `FileMemoryStore`
- `MemoryRecord`
- `prefetch -> collect` bounded recall
- resident entrypoint/index -> manifest/header -> payload layering
- payload taxonomy 与 overlay scope 两条独立轴
- `AutoMemoryRuntimeConfig` / `AutoMemoryRuntime`
- `direct_write / extract / dream` 三条写路径

当前 runtime 的关系是：

- `session` 提供 transcript、resume、short-term memory
- `SimpleHarness` 在 `build_model_input(...)` 阶段读取 durable memory
- runtime 把 recalled durable memory 注入 `ModelTurnRequest.memory_context`
- post-turn maintenance 可以调度 extract / consolidation，但 durable memory 不得覆盖历史 transcript

当前 durable-memory 的分层语义是：

1. resident entrypoint/index
2. manifest/header candidates
3. recalled payloads

当前 durable-memory 的写路径是：

1. direct write
2. turn-end extract
3. dream/background consolidation
