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
- instruction markdown loading with hierarchical `AGENTS.md`

## 当前不支持

- provider-native prompt cache integration
- model-specific context packing strategies
- richer rule authoring beyond current local markdown loader baseline
