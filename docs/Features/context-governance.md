# Context Governance

当前 context governance 负责 context shaping、预算规划和 bootstrap prompt 组装。

## 当前支持

- token estimate baseline
- warning threshold
- continuation budget planning
- recommended output-token reservation
- proactive compact
- reactive overflow recovery
- long tool result externalization
- prompt-cache-aware shaping baseline
- provider cache key baseline
- prompt-cache stable-prefix / dynamic-suffix baseline
- prompt-cache break detection baseline
- prompt-cache fork-sharing baseline
- prompt-cache strategy-equivalence baseline
- harness-level `last_context_report`
- harness-owned bootstrap prompt baseline
  - OpenAgent identity / role
  - local-first operating mode
  - workspace root projection
  - tool usage contract
  - static / dynamic section split baseline

## 当前不支持

- provider-native prompt cache integration
- model-specific token accounting

