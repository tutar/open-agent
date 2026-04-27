# Durable Memory

`durable_memory/` 当前负责 cross-session durable recall、layered selection 与 consolidation，不属于 `session/`。

## 当前支持

- `InMemoryMemoryStore`
- `FileMemoryStore`
- payload taxonomy:
  - `user`
  - `feedback`
  - `project`
  - `reference`
  - `note`
- overlay family:
  - `user`
  - `project`
  - `team`
  - `agent`
  - `local`
- resident entrypoint/index -> manifest/header -> payload 的 bounded recall baseline
- direct write / transcript extract / dream consolidation 三条独立写路径
- auto-memory runtime gate:
  - recall
  - direct write
  - extract
  - dream
- transcript-to-durable-memory extraction baseline
- background consolidation job baseline
- OpenClaw-style dreaming memory:
  - Light / REM / Deep phase sweep
  - `memory/.dreams/` machine state
  - phase reports under `DREAMS.md` and `memory/dreaming/<phase>/YYYY-MM-DD.md`
  - optional `MEMORY.md` promotion artifact
  - deterministic Dream Diary entries that are explicitly not promotion sources
- bounded recall into `ModelTurnRequest.memory_context`
- same-agent cross-session long-term memory recall baseline
- restart-safe durable memory recall
- role-bound durable-memory root under `roles/<role_id>/memory`
- role memory write-back / dreaming / consolidation via the same durable-memory pipeline

## 当前不支持

- richer ranking and manifest selection policy
- richer extraction heuristics beyond the local baseline
- a fuller team runtime around the `team` overlay
- external cron/daemon integration for dreaming beyond the runtime-local scheduler
