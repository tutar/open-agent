# Context Engineering

当前 `ContextEngineering` 负责模型输入前的上下文装配，而不只是 compact helper。

它收口在 `harness/context_engineering/`，分成四个稳定子面：

- `entry`
  - bootstrap prompts
  - startup / resume / turn-zero context
- `assembly`
  - structured context planes
  - attachments / evidence refs
  - capability exposure
- `governance`
  - budget analysis
  - compact / overflow recovery
  - tool-result externalization
  - prompt-cache strategy
- `instruction_markdown`
  - role `USER.md`
  - `AGENTS.md` / `RULES.md`
  - include expansion
  - conditional rules

## 当前支持

- section-based bootstrap prompt assembly
- startup context lifecycle
- context assembly pipeline
- system / user / attachment / evidence planes
- short-term memory 与 durable-memory recall 分层
- context governance report
- continuation budget planning
- proactive compact
- overflow recovery
- long tool-result externalization
- prompt-cache stable-prefix / dynamic-suffix baseline
- prompt-cache break detection
- instruction markdown loading with role `USER.md` + hierarchical `AGENTS.md`

## Role Instruction Layer

- role 的主身份指令入口现在是 `OPENAGENT_ROOT/roles/<role_id>/USER.md`
- `ROLE.md` 只作为机器可读资源包装器，不直接进入主 instruction assembly
- 组合顺序保持：
  - role `USER.md`
  - workspace / subtree `AGENTS.md`
  - workspace / subtree `RULES.md`

## Provider-Facing System Assembly

- startup context 继续保留在 lifecycle plane 和 model-io capture 中
- 但它不再作为额外的 `role=system` message 进入 provider-facing message stream
- OpenAI-compatible backends 现在只会收到一个 system 前缀：
  - bootstrap prompt
  - startup context fragments
  - short-term memory summary
  - durable-memory recall summary
  - other system-level context

这样可以避免本地 chat template 因多条 `role=system` message 报
`System message must be at the beginning`

## User Message Retention

- compact / overflow recovery 现在都会保留最近一条真实 `role=user` message
- provider-facing request 也会在发送前校验至少存在一条 user message
- 这样可以避免长工具链 + overflow recovery 场景把 user query 裁掉后，再触发
  `No user query found in messages.`
- overflow recovery 仍然必须把 request 收敛回预算内
- 如果把最近一条 user message 补回后仍然超预算，recovery 会继续收缩：
  - 先移除更早的非 user 消息
  - 再在必要时截断保留下来的那条 user message
- 被 overflow recovery 截断的 user message 会带
  `truncated_by_overflow_recovery=true` 元数据，便于后续诊断

## 当前不支持

- provider-native prompt cache integration
- model-specific context packing strategies
- richer rule authoring beyond current local markdown loader baseline
