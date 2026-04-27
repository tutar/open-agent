# Observability Metrics Expansion

Status: in-progress

## Summary

在当前 `AgentObservability` / `RuntimeMetric` / `TraceSpan` / `ProgressUpdate` baseline 之上，
补齐 `runtime`、`session`、`turn`、`task`、`tool` 五类对象的统一指标口径。

本 proposal 覆盖 core metrics 设计，并补充 OTel exporter 方案。

当前代码已经落地这些子集能力：

- `OtelObservabilitySink` 已支持 `OTLP/HTTP JSON` traces / metrics / selected logs
- `.openagent` 的 `transcript.jsonl`、`events.jsonl`、`model-io` 已可投影到 Loki
- Tempo 已能看到 `interaction -> llm_request -> tool` 的内部调用链基线
- 标准化 `openagent_duration_ms` / `openagent_token_usage` 已开始发射

OTel 范围限定为：

- `OTLP` traces
- `OTLP` metrics

本 proposal 仍不包含 vendor-specific backend mapping。

重点补齐两类能力：

- `token_usage`
  - 除总量外，支持按稳定调用点和模型拆分
  - cache usage 拆分为 `cache_creation_input_tokens` 和 `cache_read_input_tokens`
- `duration_ms`
  - 明确区分 `total_duration_ms`
  - 明确区分 `total_api_duration_ms`
  - 明确区分 `total_internal_duration_ms`

## Current Gaps

- 当前 `llm_request.duration_ms`、`turn.duration_ms`、`tool.duration_ms`、`background_task.duration_ms`
  仍是分散的单点指标，没有统一 scope 语义
- 当前 LLM usage 只在 span 结束时带聚合字段，缺少按调用点、按模型的标准化 metric
- 当前 `cache_tokens` 仍是聚合概念，不能区分 cache creation 与 cache read
- 当前没有统一定义“外部 API 延迟”和“内部延迟”的边界
- 当前 `runtime` / `session` 没有标准化累计 rollup 口径
- 当前 `model-io` 仍主要保留 provider raw usage，不保证天然满足全部 normalized token bucket
- 当前 provider usage metric 仍严格依赖 provider 真正返回 usage；不会做 fallback 估算
- 当前 `transcript.jsonl` 仍不承担 tool-started / requires_action / planning 语义
- 当前 `events.jsonl` 虽已覆盖 turn/tool lifecycle，但 richer join key 仍需继续评估

## Metric Taxonomy

### Token Usage

`token_usage` 作为规范 metric kind，至少包含这些维度：

- `scope`
- `session_id`
- `task_id`
- `agent_id`
- `model`
- `callsite`
- `provider_adapter`

规范 token bucket 至少包含：

- `input_tokens`
- `output_tokens`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`
- `total_tokens`

约束如下：

- `total_tokens = input_tokens + output_tokens`
- cache 相关字段单独保留，不再使用聚合 `cache_tokens` 作为标准口径
- 如果 provider 未返回 usage，则允许该次 `token_usage` 缺失，不做推断补值
- 对 streaming provider，adapter 应主动启用 provider usage 返回能力并完整持久化
  provider exchange，避免因为请求或记录链路缺失导致 `usage=null`
- 如果 provider 只返回未拆分 cache 总量，则只保留原始值到 attributes，不映射为规范 bucket

### Duration

`duration_ms` 使用三类规范口径：

- `total_duration_ms`
  - 当前对象完整 wall-clock 耗时
- `total_api_duration_ms`
  - 当前对象内部所有外部 API / 外部服务调用耗时总和
- `total_internal_duration_ms`
  - `total_duration_ms - total_api_duration_ms`

目标不是只看 provider 延迟，而是把性能分析拆成：

- 外部延迟
- 内部延迟

## API vs Internal Duration Rules

`api` 指任何跨 OpenAgent 进程边界的外部调用。

计入 `total_api_duration_ms` 的调用包括：

- LLM / provider 请求
- MCP server 请求
- web backend 请求
- tool 内部对外 HTTP / RPC / SDK 服务调用
- 其他显式标记为 external service 的远程交互

不计入 `total_api_duration_ms` 的调用包括：

- 进程内函数调用
- 本地 session / store / durable memory 读写
- 本地文件系统操作
- 本地子进程工具执行本身

附加约束：

- 如果本地子进程内部再发起了被显式观测的外部 API，则那部分外部调用仍要计入 API duration
- `total_api_duration_ms` 以子外部调用时长求和为准，用于热点分析和归因
- 对并行外部调用，不承诺 `total_api_duration_ms` 等于真实挂钟阻塞时间
- `total_internal_duration_ms` 作为规范指标直接产出，不要求消费者自行反推

## Scope Rollup Rules

### Tool

- `tool.total_duration_ms` 表示单次 tool execution 的完整耗时
- `tool.total_api_duration_ms` 汇总该 tool 内部全部外部 API 调用
- `tool.total_internal_duration_ms` 表示 tool 内本地处理耗时
- tool 默认不产生 `token_usage`，除非该 tool 显式接入模型 usage

### Task

- `task` 指 background task / verifier task 级累计指标
- 汇总其内部 turn、tool、external API 子活动
- 在 task terminal 节点输出 rollup snapshot

### Turn

- `turn` 指单次交互生命周期指标
- 汇总本 turn 内全部 model request、tool execution、requires_action continuation 和外部 API
- 在 turn terminal 节点输出 rollup snapshot

### Session

- `session` 使用累计 rollup 模型
- 汇总当前 session 内全部 turn / task / tool / external API 指标
- 在 turn terminal、task terminal、session state 关键切换点输出快照

### Runtime

- `runtime` 使用 host 级累计 rollup 模型
- 汇总当前 runtime 生命周期内全部 session 指标
- 作为全局聚合口径，不替代 session 查询

## Callsite Enumeration

`callsite` 使用稳定枚举作为主维度，不使用源码函数路径做契约。

第一版至少覆盖：

- `turn.model_request`
- `turn.continuation_model_request`
- `background_task.model_request`
- `verifier_task.model_request`
- `turn.tool_execution`
- `background_task.tool_execution`
- `tool.external_api`
- `mcp.external_api`
- `web_fetch.external_api`
- `web_search.external_api`

约束如下：

- `callsite` 是聚合维度，不等于 span type
- `model` 与 `callsite` 必须同时保留
- 同一 session 内允许同模型不同调用点并存
- 同一调用点内允许多模型并存

## OTel Exporter

### Export Scope

OTel exporter 覆盖：

- `TraceSpan -> OTel span`
- `RuntimeMetric -> OTel metrics`

以下信号不作为第一类 OTel 主信号：

- `ProgressUpdate`
- `SessionStateSignal`

它们只允许作为补充投影：

- span events
- span attributes

### Export Architecture

OTel 继续服从当前 vendor-neutral sink 架构，不单独引入新的 exporter facade。

新增：

- `OtelObservabilitySink`

保持不变：

- `AgentObservability`
- `ObservabilitySink`
- `CompositeObservabilitySink`

约束如下：

- `AgentObservability` 继续只发出规范化 observability models
- OTel 适配逻辑只发生在 sink 层
- `CompositeObservabilitySink` 可以同时挂载本地 sink 和 `OtelObservabilitySink`
- OTel 是显式启用的可选 sink，不进入默认 development sink baseline

### OTel Mapping Rules

`TraceSpan` 映射规则：

- `trace_id`、`span_id`、`parent_span_id` 直接复用到 OTel trace context
- `span_type`、`session_id`、`task_id`、`callsite`、`model`、`provider_adapter` 进入 span attributes
- `duration_ms`、`ttft_ms`、`input_tokens`、`output_tokens`、
  `cache_creation_input_tokens`、`cache_read_input_tokens` 作为 span attributes 导出

`RuntimeMetric` 映射规则：

- `token_usage` 导出为 OTel metrics
- `total_duration_ms`、`total_api_duration_ms`、`total_internal_duration_ms` 导出为 OTel metrics
- 统一保留 attributes/tag：
  - `scope`
  - `metric_kind`
  - `callsite`
  - `model`
  - `provider_adapter`
  - `api_kind`
  - `api_target`
  - `aggregation`

`ProgressUpdate` / `SessionStateSignal` 映射规则：

- 不定义稳定的 OTel metric 契约
- 如需投影，只能作为附加 span event 或 attributes
- 下游 dashboard 不应依赖它们作为主分析面

### Activation And Config

OTel sink 通过 host/runtime 配置显式开启。

最小配置面固定为：

- `enabled`
- `otlp_endpoint`
- `service_name`
- `service_instance_id`
- `export_traces`
- `export_metrics`

默认行为：

- 未显式开启时，不创建 `OtelObservabilitySink`
- 开启后，`traces` 和 `metrics` 都允许导出
- endpoint 缺失时，OTel sink 不启用
- vendor-specific auth / header / retry policy 不展开成主设计，只允许作为 transport passthrough

## Wiring Plan

### Shared Observability Layer

在 `observability/` 顶层 shared seam 内补齐规范化 metric schema 或 helper，避免继续依赖自由字符串拼装。

`RuntimeMetric.attributes` 统一保留这些键：

- `scope`
- `metric_kind`
- `callsite`
- `model`
- `provider_adapter`
- `api_kind`
- `api_target`
- `retry_index`
- `aggregation`

同时在 `observability/` 顶层 shared seam 增加：

- `OtelObservabilitySink`
- OTel trace / metric mapping helper
- OTLP transport-facing config model

### Runtime

`harness/runtime/core/agent_runtime.py` 负责：

- 在 LLM request 成功路径发出规范化 `token_usage`
- 在 LLM request 成功和失败路径都产出 request 级 API duration
- 对支持 streaming 的 provider，在 streaming 终态组装完整 `ModelProviderExchange`
- 将当前 `llm_request.duration_ms` 归并到统一 schema

`harness/runtime/core/ralph_loop.py` 负责：

- `turn` 级 `total_duration_ms`
- `turn` 级 `total_api_duration_ms`
- `turn` 级 `total_internal_duration_ms`
- turn terminal rollup snapshot

`harness/runtime/projection/observability.py` 负责：

- 作为 runtime 到 shared observability 的统一投影面
- 承载 rollup helper，避免聚合逻辑散落在多个 runtime 分支
- 不直接感知 OTel API

### Task

`harness/task/background.py` 负责：

- `task` 级三类 duration rollup
- task terminal snapshot
- 保留 `task_id` 关联

### Tools

`tools/executor.py` 负责：

- `tool.total_duration_ms`
- `tool.total_api_duration_ms`
- `tool.total_internal_duration_ms`
- 为可观测 external API tool/backend 提供统一记录接线点

### Session And Runtime Rollup

`session` 和 `runtime` 使用累计 rollup 模型：

- 累加下层 turn / task / tool / request 指标
- 在关键 lifecycle 节点输出快照
- 总量必须等于其子级规范分量累加结果

### Host Assembly

host / runtime assembly 负责：

- 基于配置决定是否装配 `OtelObservabilitySink`
- 继续通过 `CompositeObservabilitySink` 与 `StdoutObservabilitySink` /
  `InMemoryObservabilitySink` 共存
- 未启用 OTel 时保持当前默认行为不变

## Testing And Acceptance

至少覆盖以下测试场景：

- 单次普通 turn
  - 产生 `turn.total_duration_ms`
  - 产生 `turn.total_api_duration_ms`
  - 产生 `turn.total_internal_duration_ms`
  - 满足 `total = api + internal`
- provider 返回 cache 细分 usage
  - 正确记录 `cache_creation_input_tokens`
  - 正确记录 `cache_read_input_tokens`
  - 不再把二者压成单一 `cache_tokens`
- provider 仅返回普通 input/output usage
  - `token_usage` 仍正常产出
  - cache 细分字段为空
- 单次 turn 内多次 model request
  - `turn` 聚合值等于各 request 求和
  - `session` 与 `runtime` 累计值同步增长
- requires_action continuation
  - continuation request 使用独立 callsite
  - 不与初始 request 混淆
- tool + llm 混合 turn
  - `tool.total_duration_ms` 单独可见
  - `turn.total_duration_ms` 包含 tool 与 llm 耗时
  - `turn.total_api_duration_ms` 包含 llm 与 tool 内外部 API 耗时
  - `turn.total_internal_duration_ms` 只表示本地处理耗时
- tool 内触发外部 API
  - 外部调用被标记为 `tool.external_api`
  - 可区分外部等待与 tool 内本地处理
- background task / verifier task
  - `task` 级累计值正确
  - `task_id` 不丢失
- provider 未返回 usage
  - duration 仍正常发出
  - token_usage 不伪造总量
- streaming provider 请求开启 provider usage 后
  - `model-io.usage` 非空
  - `openagent_token_usage` 正常产出
- 不同模型切换
  - 同一 session 内可按模型拆分汇总
  - session/runtime 总量等于各模型分量求和
- 并行外部 API
  - 子 API 调用各自记录
  - rollup 行为按规范求和，不与 wall-clock 语义混淆
- 启用 `OtelObservabilitySink`
  - `interaction`、`llm_request`、`tool`、`background_task` 都可导出为 OTel spans
  - `token_usage` 和三类 `duration_ms` 都可导出为 OTel metrics
  - 保留 `scope`、`callsite`、`model`、`api_kind` 等 attributes
- `CompositeObservabilitySink` 同时挂本地 sink 和 OTel sink
  - 不改变现有 stdout / in-memory 语义
  - OTel 导出只是附加能力
- 未启用 OTel 配置
  - 当前 observability baseline 完全不变
- cache usage 细分
  - `cache_creation_input_tokens` 与 `cache_read_input_tokens` 在 OTel 导出中可区分
- `ProgressUpdate` / `SessionStateSignal`
  - 如被投影为 span event / attributes，不影响 traces / metrics 主路径
  - 不要求下游依赖它们形成稳定 dashboard 契约

## TODO For Next Evaluation

以下缺口当前已确认存在，但本轮先保留为 TODO，下一次再决定是补源数据还是继续靠投影层推断：

- `transcript.jsonl`
  - 是否补稳定 `tool_name` / `call_id` / `agent_id`
  - 是否补更稳定的 `turn_id`
- `events.jsonl`
  - 是否补 richer `task_id` / `turn_id` 关联键
  - 是否统一补 `agent_id`
- `model-io`
  - 是否补 normalized usage 字段
  - 是否补 `callsite`
  - 是否补 `ttft_ms` / request duration
- `AnthropicMessagesModelAdapter`
  - 当前仍是 non-streaming；若后续补 streaming，必须同步满足完整 exchange/usage capture 契约

## Non-Goals

- 不在本 proposal 内设计 vendor-specific backend mapping
- 不在本 proposal 内定义持久化 trace storage
- 不在本 proposal 内要求所有 tool 立刻具备外部 API 精细埋点；本次先定义统一口径和接线面
- 不把 `ProgressUpdate` 或 `SessionStateSignal` 升级成独立 OTel 主信号

## Defaults And Assumptions

- 本 proposal 包含 core metrics 和 `OTLP` exporter 设计
- `callsite` 采用稳定枚举作为主维度
- `runtime` 与 `session` 使用累计 rollup，并在关键生命周期节点输出快照
- cache usage 必须拆分为 `cache_creation_input_tokens` 与 `cache_read_input_tokens`
- `api` 统一表示外部 API / 外部服务调用，目的就是把性能问题拆成外部延迟与内部延迟
- OTel 以可选 `OtelObservabilitySink` 形式接入，而不是替换现有 sink 架构
