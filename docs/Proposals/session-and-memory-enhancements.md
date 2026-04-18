# Session And Memory Enhancements

Status: proposed

## Summary

补齐 session durability、short-term memory policy 和 durable memory recall/ consolidation 的剩余缺口。

## Current State

- file-backed session/event log baseline 已实现
- single active harness lease 已实现
- short-term memory persistence baseline 已实现
- durable memory recall / consolidation baseline 已实现

## Remaining Gaps

- richer append-only event log semantics and restore modes
- branch-aware replay if required by spec
- richer session lifecycle side-state restore
- short-term memory salience / eviction / partial coverage policy
- deeper extraction policy for durable memory
- background dream / cross-session consolidation
- richer recall ranking and scoping

