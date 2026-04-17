# Model Provider And Providers

`ModelProviderAdapter` 是 harness 对模型层暴露的稳定接口。

当前 `openagent` 把真实 provider 实现放在：

- `src/openagent/harness/providers`

这表示 provider adapter 是 harness 的 `model provider` 子层，而不是 agent 根级公共模块。

## Layering

当前分层是：

`RalphLoop -> ModelProviderAdapter -> harness/providers -> provider HTTP API`

其中：

- `RalphLoop`
  负责 turn 状态机
- `ModelProviderAdapter`
  负责标准化输入输出语义
- `harness/providers`
  负责 OpenAI-compatible / Anthropic-compatible 的协议映射

## What Providers Own

provider adapter 负责：

- base URL
- auth header
- request payload 组装
- response payload 解析
- tool call wire shape 映射
- provider error 归一化 baseline

## What Providers Do Not Own

provider adapter 不负责：

- tool execution
- requires_action
- session 恢复
- context compact
- gateway projection

这些仍然在 harness、session、tools 和 gateway 层。

## Current Python Mapping

当前已实现：

- `OpenAIChatCompletionsModelAdapter`
- `AnthropicMessagesModelAdapter`
- `load_model_from_env`
- `UrllibHttpTransport`

openagent host 在检测到 `OPENAGENT_MODEL` 时，会优先通过 `load_model_from_env()` 装配真实 provider。
