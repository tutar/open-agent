# Providers

当前 `openagent` 使用统一的 Instructor-backed provider adapter。

## 当前支持

- `InstructorModelAdapter`
- `load_model_from_env()`
- Instructor `Mode.TOOLS` structured extraction
- internal `Respond` action for non-tool turns
- OpenAI-compatible / Anthropic provider-facing message projection
- tool result replay projection:
  - OpenAI-compatible: `role="tool"` + `tool_call_id`
  - Anthropic: `tool_result` + `tool_use_id`
- streaming `assistant_delta` aggregation
- `create_with_completion(...)` based raw completion capture
- `create_partial(...)` based streaming structured extraction
- 自定义 `OPENAGENT_BASE_URL`
- `OPENAGENT_PROVIDER` 缺省时的 provider inference

这些 provider adapter 归属 `harness/providers`。当前 OpenAgent host 会在设置 `OPENAGENT_MODEL` 时自动尝试加载真实 provider，并通过 Instructor `Mode.TOOLS` 统一处理：

- tool turn: 返回单个 typed tool action，并映射成 `ToolCall`
- non-tool turn: 返回内部 `Respond` action，并映射成 `assistant_message`
- response model 构建与 action 解析在 adapter 内部通过 Pydantic object models 完成

## 当前不支持

- provider-specific advanced options 全量映射
- 不依赖 provider SDK 的纯 stdlib structured-output path
