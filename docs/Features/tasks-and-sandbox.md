# Tasks And Sandbox

## Tasks

当前 local task baseline 支持：

- generic task
- background task handle
- verifier task handle
- local background agent orchestrator
- local verification runtime
- task registry / implementation registry
- checkpoint / progress / terminal task events
- complete / fail / kill
- file-backed event persistence
- `output_ref + output_cursor`
- `read_events / read_output / await / kill`
- terminal notification dedupe via `notified`
- chat/session observer retention and eviction
- file-backed task persistence
- restart-safe task/handle recovery

task 子域不再承担 multi-agent routing/projection。

delegated worker identity、task notification、direct-view input、viewed transcript 现在属于
`harness/multi_agent/`。

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
