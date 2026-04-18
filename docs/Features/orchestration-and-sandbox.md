# Orchestration And Sandbox

## Orchestration

当前 orchestration 支持本地 baseline：

- generic task
- background task handle
- verifier task handle
- local background agent orchestrator
- checkpoint
- complete
- fail
- file-backed task persistence
- restart-safe task/handle recovery

## Sandbox

当前 sandbox 是本地 allowlist baseline。

### 已支持

- `LocalSandbox`
- 命令前缀 allowlist
- capability negotiation
- network / filesystem / credential deny reasons
- 本地测试场景执行

### 当前不支持

- 更细的凭证边界
- 更细的网络边界
- provider-specific sandbox capability negotiation
