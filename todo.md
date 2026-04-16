# TODO

## Completed Foundation

- Project scaffolding is in place with `uv`, `pytest`, `ruff`, `mypy`, and GitHub CI gating merges to `main`
- Product scope is now explicit: this Python SDK targets local terminal/TUI hosts and uses in-process direct calls
- The terminal frontend lives under `agent-python-sdk/frontend/terminal-tui` and talks to the agent through a gateway layer
- Canonical object model baseline exists with enums, schema envelope, JSON-like serialization, and public exports
- Minimal local runtime baseline exists:
  - in-memory and file-backed session store
  - static tool registry and simple tool executor
  - minimal harness with `turn_started / assistant_delta / tool_started / tool_progress / tool_result / tool_failed / tool_cancelled / requires_action / turn_completed / turn_failed`
  - approval continuation via `continue_turn(...)`
  - model streaming via `stream_generate(...)`
  - local timeout / retry / cancellation baseline
  - in-memory task manager
  - local sandbox baseline
  - `TuiProfile` runtime assembly
  - local ingress gateway and in-process session adapter baseline
  - frontend-ready terminal TUI built with `React + Ink + Yoga`
  - stdio bridge between TUI and gateway
  - multi-session terminal workflow with session list / switch / replay
- Conformance baseline exists for:
  - `basic-turn`
  - `tool-call-roundtrip`
  - `requires-action-approval`
  - `session-resume`
  - `mcp-tool-adaptation`
  - `memory-recall-and-consolidation`
  - `background-agent`
- Golden replay baseline exists for the above scenarios using `agent-sdk-spec/conformance/golden`
- Ecosystem baseline exists for:
  - command shared model
  - file skill discovery / activation / bridge
  - transport-backed MCP client seam plus deterministic in-memory transport
  - prompt/skill adapters
- Memory baseline exists for:
  - in-memory and file-backed short-term session memory stores
  - safe-point short-term memory update and stabilization
  - short-term memory injection into `ModelTurnRequest.short_term_memory`
  - in-memory and file-backed durable memory stores
  - transcript-to-memory consolidation
  - scoped durable memory via `user / project / agent / local`
  - recall into `memory_context` without transcript rewrite
- Capability surface baseline now includes:
  - capability descriptors with origin and host projection
  - model-visible vs user-visible projection
  - host filtering for `tui` vs `desktop`
- Orchestration baseline now includes:
  - background task handles
  - verifier task handles
  - local background agent orchestrator
  - checkpoint / complete / fail lifecycle
  - file-backed task persistence and restart-safe recovery
- Gateway baseline now includes:
  - file-backed session binding persistence across gateway restarts
- Tools baseline now includes:
  - explicit non-concurrency-safe scheduling coverage
  - pluggable policy-engine seam on top of per-tool `allow/deny/ask`
  - rule-based policy-engine baseline

## Spec Update: Hosting Profiles And Deployment Boundaries

- Keep the Python SDK intentionally scoped away from `Cloud`
  - do not implement remote bindings, wake/resume orchestration, or remote execution targets
  - do not introduce IPC/daemon layering unless a concrete local bottleneck proves it necessary
- Keep active delivery focused on terminal/TUI work
- Apply the updated spec selectively for local hosts
  - keep streaming/event semantics where useful
  - but allow direct in-process function calls as the primary runtime path for terminal/TUI
- Make gateway the frontend integration boundary
  - frontend channel adapters should talk to `IngressGateway`
  - gateway should own session binding, normalization, and egress projection
  - frontend should not directly call harness/runtime internals
- Refactor current `TuiProfile` documentation and interfaces to reflect:
  - TUI is a local host profile
  - session/orchestration/sandbox can be optimized for same-process execution in this SDK
- Keep a lightweight local binding abstraction only for clarity and future refactor safety
- Audit existing interfaces for local performance and simplicity rather than remote-capable semantics

## Remaining Gaps By Module

### Harness

- Deepen timeout, cancellation, retry, and partial-failure semantics beyond the current local baseline
- Add context assembly pipeline beyond raw message lists
- Add post-turn processing and extension hook surfaces

### Context Governance

- Harden long tool result externalization beyond the current file-backed baseline
- Add provider-aware prompt-cache integration beyond the current shaping hooks

### Session

- Strengthen durable append-only event log semantics beyond the current local baseline
  - richer wake / restore mode handling
  - branch-aware replay if the spec requires it
- Add richer lifecycle state and side-state restore guarantees
- Deepen short-term memory policy
  - salience
  - eviction
  - partial transcript coverage updates

### Tools

- Deepen policy engine beyond the current rule-based baseline
- Add capability origin metadata required by `capability-surface.md`
  - builtin
  - bundled
  - plugin
  - user / project
  - managed
  - mcp / remote

### Skills / Commands / MCP

- Harden runtime projection between:
  - MCP tools -> Tool
  - MCP prompts -> Command
  - MCP skills -> Skill

### Memory

- `memory` now lives under `session.memory`; it is not a top-level package anymore
- Deepen extraction policy beyond the current transcript slice baseline
- Add background dream / cross-session consolidation semantics
- Add richer recall ranking and scoping

### Sandbox

- Expand from local command allowlist to spec-aligned sandbox contracts
  - execution sandbox
  - credential / network boundary

### Gateway

- Harden the explicit `terminal-tui` channel adapter baseline
- Extend control routing from baseline support to richer host mode semantics
- Finish Feishu group-chat E2E after app-side group message event delivery is enabled
  - private-chat E2E is already automated and validated with `lark-cli --as user --chat-id <p2p_chat_id>`
  - group-chat test path exists, but real verification is still blocked until the Feishu app actually receives group raw events

### Orchestration

- Keep orchestration scoped to local/background task execution for terminal/TUI

## Remaining Conformance Work

- Expand conformance beyond the current first four cases
- Add prompt-cache-related conformance cases once context governance exists
- Add compatibility tests for different model capability profiles
  - native tool calling
  - no native tool calling
  - structured output present / absent

## Short-Term Priorities

- Deepen context governance with richer budgeting and provider-aware prompt cache integration
- Expand conformance beyond the current first four local cases

## Delivery Guardrails

- Keep GitHub CI required for merges to `main`
- Keep `pytest`, `ruff check`, `ruff format --check`, and `mypy` as the minimum gate
- Extend CI once additional conformance suites and profile-specific tests land


# todo
skills/mcp 
- tui的bridge可以去掉吗？直接连host的端口不行吗
- agent的规划能力怎么构建