# Session

`session/` 当前只负责 session durable state 与 short-term continuity，不再承载 durable memory。

## 当前支持

- `FileSessionStore`
- `InMemoryShortTermMemoryStore`
- `FileShortTermMemoryStore`
- append-only transcript log baseline
- append-only event log baseline
- session checkpoint / cursor baseline
- wake / resume snapshot baseline
- working-state restore inputs
- single active harness lease
- short-term memory safe-point update and stabilization
- terminal TUI 的多 session 切换与 replay

## 当前不支持

- richer branch / sidechain transcript graph
- richer short-term salience / eviction policy
- more explicit restore mode matrix

## 持久化边界

- transcript 是 agent-owned append-only turn 记录
- session 自己只保存 `transcript.ref`
- session state 单独保存非 transcript 字段
- runtime event log 继续独立保存运行时事件

当前三份事实源的职责固定为：

- `agent_<role_id|default>/<agent_id>/transcript.jsonl`
  - `user / assistant / tool-result` 视图
- `sessions/<session_id>/events.jsonl`
  - turn / tool 生命周期与 streaming delta
- `agent_<role_id|default>/<agent_id>/model-io`
  - provider token usage、reasoning、streaming、request/response 证据

role-bound durable memory 作为第四条长期事实源，固定落在：

- `roles/<role_id>/memory/`
  - role durable-memory recall / write-back / dreaming / consolidation 根

当前默认落盘结构：

- `sessions/<session_id>/state.json`
- `sessions/<session_id>/events.jsonl`
- `sessions/<session_id>/transcript.ref`
- `agent_<role_id|default>/<agent_id>/transcript.jsonl`
