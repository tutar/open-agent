# Observability And Model I/O

这一页聚合 OpenAgent 的 observability 与默认开启的模型输入输出沉淀。

## Observability

当前 observability 子系统已经独立于 runtime event log。

### 当前支持

- `AgentObservability`
- `RuntimeMetric`
- `SessionStateSignal`
- `ProgressUpdate`
- span-based tracing via `start_span(...) / end_span(...)`
- `OtelObservabilitySink`
- `InMemoryObservabilitySink`
- `StdoutObservabilitySink`
- `NoOpObservabilitySink`
- `CompositeObservabilitySink`
- interaction / llm request / tool / background task span baseline
- interaction -> llm request -> tool parent/child trace chain baseline
- session lifecycle signal: `running / requires_action / idle`
- task / background progress projection baseline
- host-local structured stdout output for debugging
- OTLP/HTTP JSON export for traces / metrics / selected runtime logs
- standardized `openagent_duration_ms` metric emission
- standardized `openagent_token_usage` metric emission
- standardized `openagent_token_usage_total` cumulative counter emission
- callsite/model-scoped token usage emission when provider exposes usage
- Grafana runtime-overview token aggregation can be computed as selected-range usage via
  `increase(openagent_token_usage_total[window])`
- cache usage split into `cache_creation_input_tokens` and `cache_read_input_tokens`
- OpenAI-compatible streaming requests explicitly ask providers for usage via
  `stream_options.include_usage`

### 当前不支持

- vendor-specific tracing backend integration
- standalone durable trace storage
- precise provider token/cost accounting when the provider does not expose it

## Model I/O Capture

当前 OpenAgent 默认开启 agent 级模型输入输出沉淀。

### 当前支持

- file-backed model dataset capture under `.openagent/agent_<role_id|default>/local-agent/model-io`
- append-only `index.jsonl`
- per-call record files under `records/<session_id>/`
- assembled `ModelTurnRequest` capture
- provider payload capture
- provider raw response capture
- parsed `ModelTurnResponse` capture
- provider-exposed reasoning / thinking block capture
- non-streaming and streaming final result capture
- streaming provider exchange capture including provider payload, raw streamed events, and
  provider-reported usage when available
- error-path capture for provider failure / timeout / retry exhaustion
- host defaults derived from `OPENAGENT_ROOT` and optional `OPENAGENT_ROLE_ID`
- provider failures are also echoed to the local host console with session id, adapter, retry index,
  and the model-io capture root so local debugging does not depend on opening the card output first
- Loki-facing projection of:
  - `.openagent/.../transcript.jsonl` as conversation view
  - `.openagent/sessions/<session_id>/events.jsonl` as runtime event view
  - `.openagent/.../model-io` as provider evidence view

### 当前不支持

- automatic retention cleanup
- transcript-level redaction policy
- provider-hidden reasoning recovery
- guaranteed normalized usage for providers that only expose partial/raw token fields
- Anthropic-compatible streaming model-io capture; the current adapter remains non-streaming
