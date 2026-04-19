# Providers

当前已经有真实 LLM provider adapter。

## 当前支持

- `OpenAIChatCompletionsModelAdapter`
- `AnthropicMessagesModelAdapter`
- `load_model_from_env()`
- stdlib-based `UrllibHttpTransport`
- `OPENAGENT_PROVIDER=openai`
- `OPENAGENT_PROVIDER=anthropic`
- 自定义 `OPENAGENT_BASE_URL`
- provider-level tool definition projection
- provider response -> `ToolCall` 解析 baseline
- OpenAI-compatible `stream_generate(...)`
- OpenAI-compatible SSE / chunked streaming parsing
- `stream: true` payloads for OpenAI-compatible streaming requests
- streamed assistant delta aggregation into final `assistant_message`

这些 provider adapter 当前归属 `harness/providers`，而不是 agent 根目录。当前 OpenAgent host 会在设置 `OPENAGENT_MODEL` 时自动尝试加载真实 provider。

## 当前不支持

- Anthropic-compatible streaming path
- provider-specific advanced options 全量映射
