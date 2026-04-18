# Session And Memory

Session 与 Memory 当前分别负责 session continuity 和 durable memory，但在运行时会一起参与上下文组装。

## Session

当前 session 子系统支持本地内存和文件两种存储。

### 当前支持

- `InMemorySessionStore`
- `FileSessionStore`
- `InMemoryShortTermMemoryStore`
- `FileShortTermMemoryStore`
- append-only event log baseline
- session checkpoint baseline
- session cursor baseline
- restore marker baseline
- wake / resume snapshot baseline
- event replay baseline
- approval continuation state
- single active harness lease
- short-term session memory persistence
- short-term memory safe-point update and stabilization
- terminal TUI 的多 session 切换与 replay

### 当前不支持

- 分支化 event log
- 更完整的 wake / restore mode 设计
- richer short-term salience / eviction policy

## Memory

当前 memory 子系统负责 durable memory，而不是 session continuity。

### 当前支持

- `InMemoryMemoryStore`
- `FileMemoryStore`
- scoped durable memory via `user / project / agent / local`
- same-agent cross-session long-term memory recall baseline
- transcript-to-durable-memory consolidation baseline
- background consolidation job baseline
- recall into `ModelTurnRequest.memory_context`
- `AGENTS.md` file-backed context injection with home -> workdir -> subtree precedence
- restart-safe durable memory recall

### 当前不支持

- cross-session dream consolidation
- richer extraction policy
- richer recall ranking and scoping

