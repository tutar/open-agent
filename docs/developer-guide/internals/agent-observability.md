# Agent Observability

`AgentObservability` 是当前 OpenAgent 中独立于 `RuntimeEvent` 的观测平面。

注意：它现在也独立于 `.openagent/agent_default/local-agent/model-io` 这层模型原始数据沉淀。

## Why It Exists

`RuntimeEvent` 解决的是：

- turn 内发生了哪些业务事件
- session 如何 replay
- frontend 如何消费可投影事件

它不擅长解决：

- 调试时快速看见当前 session 状态
- model request / tool call / background task 的 span 关系
- 本地 host 直接打印结构化观测信息
- 默认长期保留完整模型输入输出数据

因此 observability 被单独抽出来。

## Current Layers

当前 observability 明确分三层：

- `SessionStateSignal`
  - `running`
  - `requires_action`
  - `idle`
- `ProgressUpdate`
  - turn
  - tool
  - task
  - background agent
- `TraceSpan`
  - interaction
  - llm request
  - tool
  - background task

并且当前已经接入两条外部可视化路径：

- `OTLP traces / metrics`
  - 面向 Tempo / Prometheus / Grafana
- `.openagent` 数据投影到 `OTLP logs`
  - 面向 Loki / Grafana logs

## Main Wiring

当前主要接线位置：

- `harness/runtime/core/agent_runtime.py`
  - context governance report
  - llm request metric/span
- `harness/runtime/core/ralph_loop.py`
  - interaction span
  - session lifecycle signal
  - turn duration metric
- `harness/runtime/projection/`
  - runtime 到顶层 observability 的桥接
- `SimpleToolExecutor`
  - tool span
  - tool progress projection
  - tool duration metric
  - tool runtime event 持久化关联 `task_id`
- `LocalBackgroundAgentOrchestrator`
  - task progress
  - background task span
  - task lifecycle external event projection
- `FileSessionStore`
  - transcript / event append-only persistence
  - transcript / runtime event Loki 投影
- `FileModelIoCapture`
  - model-io append-only persistence
  - provider evidence Loki 投影
  - streaming provider exchange persistence, including provider payload, raw streamed events,
    and provider-reported usage when exposed

## Sinks And Export

当前默认 sink 是 vendor-neutral 的：

- `NoOpObservabilitySink`
- `InMemoryObservabilitySink`
- `StdoutObservabilitySink`
- `CompositeObservabilitySink`
- `OtelObservabilitySink`

本地开发默认更偏向 `StdoutObservabilitySink`，目的是在接入新 channel 或排查 host 问题时，
不必先接外部 tracing 平台就能看见发生了什么。

当配置 `OPENAGENT_OTLP_HTTP_ENDPOINT` 时：

- runtime metrics 会导出到 OTLP metrics
- completed spans 会导出到 OTLP traces
- progress / session state / external event 会导出到 OTLP logs
- `.openagent` 的 transcript / events / model-io 也会额外投影到 OTLP logs

当前 provider 边界：

- OpenAI-compatible adapter 的 streaming 路径会显式请求 provider usage，并把完整
  streaming exchange 写入 `model-io`
- Anthropic-compatible adapter 当前仍走 non-streaming exchange capture

## Boundary

当前 observability：

- 不替代 session event log
- 不替代 `.openagent/agent_default/local-agent/model-io` 模型数据集
- 不单独做 durable trace storage
- 不负责质量评估或 correctness judgement

它只回答一件事：

- 运行时刚才发生了什么

而 `.openagent/agent_default/local-agent/model-io` 回答的是另一件事：

- 这次到底给模型发了什么，模型原始返回了什么

## Runtime Projection Boundary

runtime 不直接拥有 observability 的实现层。

当前分层是：

- `harness/runtime/projection/`
  - 负责 runtime 侧调用点和状态投影 helper
- 顶层 `observability/`
  - 负责 sink、span、metric、progress、session signal 的共享定义和实现

这样 `runtime` 可以保持自己的结构清晰，同时不把 observability 从 shared seam 挪进
`harness/`。
