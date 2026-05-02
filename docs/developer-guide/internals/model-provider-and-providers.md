# Model Provider And Providers

`ModelProviderAdapter` 是 harness 对模型层暴露的稳定接口。

当前 `openagent` 把真实 provider 实现放在：

- `src/openagent/harness/providers`

这表示 provider adapter 是 harness 的 `model provider` 子层，而不是 agent 根级公共模块。

## Layering

当前分层是：

`RalphLoop -> ModelProviderAdapter -> harness/providers -> Instructor SDK -> provider API`

其中：

- `RalphLoop`
  负责 turn 状态机
- `ModelProviderAdapter`
  负责标准化输入输出语义
- `harness/providers`
  负责 Instructor `Mode.TOOLS` client 装配、provider-facing message projection、action 到 runtime response 的转换

## What Providers Own

provider adapter 负责：

- base URL
- provider 识别与 client 初始化
- provider-facing request payload 组装
- structured action / streaming partial 解析
- tool call wire shape 映射
- provider error 归一化

## What Providers Do Not Own

provider adapter 不负责：

- tool execution
- requires_action
- session 恢复
- context compact
- gateway projection
- bootstrap prompt 内容定义

这些仍然在 harness、session、tools 和 gateway 层。

## Current Python Mapping

当前已实现：

- `InstructorModelAdapter`
- `load_model_from_env`
- `ProviderConfigurationError`
- `ProviderError`
- harness-owned bootstrap/system prompt projection

当前 provider 层使用 Instructor `Mode.TOOLS`：

- 可用 tool 会被动态编译成 typed action models
- 非 tool 回复通过内部 `Respond` action 表达
- provider 内部会先经过 schema compiler / action registry / action parser 三层，再交给 adapter 编排
- adapter 再把 action 规范化为 `ModelTurnResponse`

openagent host 在检测到 `OPENAGENT_MODEL` 时，会优先通过 `load_model_from_env()` 装配真实 provider。

当前 bootstrap/system prompt 由 harness 统一组装，再投影给 provider：

- OpenAI-compatible：作为 `messages` 中的 `role=system`
- Anthropic-compatible：作为顶层 `system`
