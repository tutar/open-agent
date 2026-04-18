# Context Governance Enhancements

Status: proposed

## Summary

继续增强 context governance，重点放在 provider-aware prompt cache 和更稳健的长结果外置策略。

## Current State

- budgeting、compact、overflow recovery 已实现
- bootstrap prompt baseline 已实现
- prompt-cache-aware shaping baseline 已实现

## Remaining Gaps

- provider-native prompt cache integration
- stronger long tool result externalization semantics
- model-specific token accounting beyond the current estimate baseline

