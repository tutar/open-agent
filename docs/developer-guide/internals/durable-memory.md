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
- OpenClaw-style dreaming pipeline:
  - `durable_memory/dreaming/models.py` 定义 dreaming config、phase、short-term recall entry 与 promotion candidate
  - `durable_memory/dreaming/state.py` 维护 `memory/.dreams/short-term-recall.json`、`phase-signals.json`、checkpoint 与 lock
  - `durable_memory/dreaming/phases.py` 运行 Light / REM / Deep 阶段
  - `durable_memory/dreaming/markdown.py` 维护 `DREAMS.md`、`MEMORY.md` 与 `memory/dreaming/<phase>/YYYY-MM-DD.md`

当前 runtime 的关系是：

- `session` 提供 transcript、resume、short-term memory
- `SimpleHarness` 在 `build_model_input(...)` 阶段读取 durable memory
- runtime 把 recalled durable memory 注入 `ModelTurnRequest.memory_context`
- post-turn maintenance 可以调度 extract / consolidation，但 durable memory 不得覆盖历史 transcript
- role-bound runtime 默认把 durable-memory store 绑定到 `roles/<role_id>/memory`
- 同一条 role memory 链路同时承担 recall、turn-end write-back、dreaming 和 consolidation

当前 durable-memory 的分层语义是：

1. resident entrypoint/index
2. manifest/header candidates
3. recalled payloads

当前 durable-memory 的写路径是：

1. direct write
2. turn-end extract
3. dream/background consolidation

`dream` 现在是阶段化的后台巩固路径。Light 阶段整理近期 session/daily
signals 并记录 reinforcement；REM 阶段抽取主题和反思信号；Deep 阶段按
frequency、relevance、query diversity、recency、consolidation、conceptual
richness 加权打分，通过阈值后把同一批 promotion candidates 写入
`MemoryStore`。`MemoryStore` 仍然是 runtime recall 的 canonical 来源；
Markdown 产物用于审计、迁移和人读 UI，不作为召回的唯一事实来源。

默认的 runtime dreaming config 是 disabled。普通 post-turn maintenance 继续执行
轻量 extract / short-term memory update；完整 dreaming sweep 只通过显式
`dream(...)`、`write_path=DREAM` 的 scheduled job，或 harness 的
`maybe_schedule_dreaming()` 时间门控入口触发。
