# Context Engineering

`ContextEngineering` 负责把 runtime 的原始输入整理成真正送给模型的 request。

它不是单一 helper，而是 `harness/context_engineering/` 下的四层子域：

- `entry/`
  - bootstrap prompts
  - startup / resume / turn-zero context
- `assembly/`
  - context planes
  - attachments / evidence refs
  - capability exposure
- `governance/`
  - token budget
  - compaction
  - overflow recovery
  - content externalization
  - prompt-cache strategy
- `instruction_markdown/`
  - role `USER.md` + `AGENTS.md` / `RULES.md` loading
  - include expansion
  - conditional rules

## Runtime Boundary

`SimpleHarness.build_model_input(...)` 现在只负责收集上游状态：

- transcript
- short-term memory
- durable-memory recall
- runtime metadata
- available tools

然后把这些输入交给 `ContextAssemblyPipeline`，由它产出：

- `system_prompt`
- normalized message stream
- startup contexts
- structured system/user context
- attachments / evidence refs
- prompt sections / prompt blocks

## Layering Rules

- transcript 仍然是 session truth source
- short-term memory 只提供 continuity summary
- durable-memory recall 仍然通过 `memory_context` 进入 request，不回写 transcript
- instruction markdown 属于规则层，不属于 durable memory
- startup context 属于 entry plane，不直接塞进 transcript
- startup context 也不再作为额外的 `role=system` message 进入 OpenAI-compatible
  provider payload；provider-facing request 统一收成单一 system 前缀
- role `USER.md` 是身份基线，优先于 workspace 层 `AGENTS.md/RULES.md`
- `ROLE.md` 不直接进入 provider-facing instruction 文本

## Governance And Editing

治理层继续负责：

- token estimate
- warning threshold
- proactive compact
- overflow recovery
- continuation budget planning
- prompt-cache snapshot / break detection
- long tool-result externalization

tool 结果外化和 compaction rewrite 现在都被视为 `context_engineering/governance/` 的 editing plane。

此外，governance trimming 现在有一个稳定 invariant：

- compact / overflow recovery 不能把最后一条真实 `role=user` message 裁掉
- provider adapter 发送前也会再次校验 request 里仍存在 user message
- overflow recovery 不能因为保留 user message 而失去预算收敛能力
  - 如果保留最近 user 后仍然超预算，会继续移除更早的非 user 消息
  - 还不够时，会截断这条被保留的 user message 内容

这条约束用于避免长工具链 overflow 后把 transcript 尾部压成只有
`assistant/tool`，最终触发上游 chat template 的
`No user query found in messages.`
