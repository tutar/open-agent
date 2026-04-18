# Harness Runtime Follow-ups

Status: proposed

## Summary

当前 harness/runtime 主链路已经落地，但仍缺少更深的运行时恢复与扩展能力。

## Current State

- turn runtime、timeout、retry、cancellation baseline 已实现
- context assembly 已经存在，不再属于待办
- bootstrap prompts、model I/O capture、approval continuation 已实现

## Remaining Gaps

- deeper timeout / cancellation / retry semantics
- partial-failure handling beyond the current local baseline
- post-turn processing hooks
- extension hook surfaces around turn lifecycle

