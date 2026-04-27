# Multi-Agent

`harness/multi_agent/` 是 OpenAgent 本地 multi-agent 子域。

它不拥有 task lifecycle；task lifecycle 仍然在 `harness/task/`。

## Ownership

`harness/multi_agent/` 负责：

- delegated worker identity
- synchronous/background delegation facade
- task-notification routing
- direct-view input routing
- viewed transcript projection

`harness/task/` 负责：

- task registry
- task implementation registry
- output/event cursor
- retention / eviction
- background task execution
- verifier task runtime

## Current Structure

- `models.py`
  - delegated identity
  - invocation
  - inter-agent message
  - viewed transcript
- `routing.py`
  - task-notification routing
  - direct-view input routing
- `delegation.py`
  - local delegated execution modes
- `projection.py`
  - viewed transcript projection
  - retention hold/release
- `runtime.py`
  - local facade used by tools/runtime assembly

## Current Runtime Boundary

本轮只做 local baseline：

- delegated subagent
- background delegation
- task notification
- viewed transcript
- direct-view input

本轮不做：

- teammate execution
- teammate mailbox runtime
- cloud orchestration
