# Agent Observability

`AgentObservability` 是当前 SDK 中独立于 `RuntimeEvent` 的观测平面。

注意：它现在也独立于 `.openagent/data/model-io` 这层模型原始数据沉淀。

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

## Main Wiring

当前主要接线位置：

- `SimpleHarness`
  - context governance report
  - llm request metric/span
- `RalphLoop`
  - interaction span
  - session lifecycle signal
  - turn duration metric
- `SimpleToolExecutor`
  - tool span
  - tool progress projection
  - tool duration metric
- `LocalBackgroundAgentOrchestrator`
  - task progress
  - background task span
  - task lifecycle external event projection

## Sinks

当前默认 sink 是 vendor-neutral 的：

- `NoOpObservabilitySink`
- `InMemoryObservabilitySink`
- `StdoutObservabilitySink`
- `CompositeObservabilitySink`

本地开发默认更偏向 `StdoutObservabilitySink`，目的是在接入新 channel 或排查 host 问题时，
不必先接外部 tracing 平台就能看见发生了什么。

## Boundary

当前 observability：

- 不替代 session event log
- 不替代 `.openagent/data/model-io` 模型数据集
- 不直接绑定 OTel
- 不单独做 durable trace storage
- 不负责质量评估或 correctness judgement

它只回答一件事：

- 运行时刚才发生了什么

而 `.openagent/data/model-io` 回答的是另一件事：

- 这次到底给模型发了什么，模型原始返回了什么
