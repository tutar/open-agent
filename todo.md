# TODO

## Completed Foundation

- Project scaffolding is in place with `uv`, `pytest`, `ruff`, `mypy`, and GitHub CI gating merges to `main`
- Product scope is now explicit: this Python SDK targets local terminal/TUI hosts and uses in-process direct calls
- The terminal frontend lives under `agent-python-sdk/frontend/terminal-tui` and talks to the agent through a gateway layer
- Canonical object model baseline exists with enums, schema envelope, JSON-like serialization, and public exports
- Minimal local runtime baseline exists:
  - in-memory and file-backed session store
  - static tool registry and policy-aware tool executor
  - minimal harness with `turn_started / assistant_delta / tool_started / tool_progress / tool_result / tool_failed / tool_cancelled / requires_action / turn_completed / turn_failed`
  - approval continuation via `continue_turn(...)`
  - model streaming via `stream_generate(...)`
  - local timeout / retry / cancellation baseline
  - in-memory task manager
  - local sandbox baseline
  - local runtime / gateway assembly helpers
  - local gateway and in-process session adapter baseline
  - frontend-ready terminal TUI built with `React + Ink + Yoga`
  - terminal host client transport between TUI and gateway
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
  - transport-backed MCP client seam
  - deterministic in-memory MCP transport
  - real stdio MCP transport
  - real streamable HTTP MCP transport with JSON / SSE parity
  - MCP protocol lifecycle baseline: `initialize -> initialized -> ping -> cancel -> close`
  - MCP auth discovery + `WWW-Authenticate` scope upgrade baseline
  - prompt/tool/resource pagination baseline
  - roots / sampling / elicitation seam baseline
  - prompt/resource/skill adapters
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
  - host filtering for `terminal` vs `feishu`
- Orchestration baseline now includes:
  - background task handles
  - verifier task handles
  - local background agent orchestrator
  - checkpoint / complete / fail lifecycle
  - file-backed task persistence and restart-safe recovery
- Gateway baseline now includes:
  - file-backed session binding persistence across gateway restarts
- Tools baseline now includes:
  - builtin tool baseline
    - `Read / Write / Edit / Glob / Grep / Bash`
    - `WebFetch / WebSearch`
    - `AskUserQuestion`
    - optional `Agent / Skill` bridge
  - explicit non-concurrency-safe scheduling coverage
  - richer tool definition metadata, aliases, provenance, and visibility
  - pluggable policy-engine seam on top of per-tool `allow/deny/ask/passthrough`
  - rule-based policy-engine baseline with denial tracking
  - streaming executor baseline
  - review command baseline

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
- Keep terminal TUI documented as a local channel client on top of the unified host
- Keep session/orchestration/sandbox optimized for same-process execution in this SDK
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

- Add a real host-integrated `WebSearch` backend instead of the current placeholder fallback
- Bridge `Agent` and review commands to orchestration by default instead of keeping them callback-only
- Deepen tool retry / recovery / cancellation semantics beyond the current local baseline

### Skills / Commands / MCP

- MCP core now lives under `src/openagent/tools/mcp/`
- Keep deepening runtime projection between:
  - MCP tools -> Tool lifecycle
  - MCP prompts -> Command
  - MCP resources -> context / observation surface
- Keep `mcp skill` explicitly separate as host extension
- Evaluate whether MCP `tasks` should move from object-model-only baseline to full runtime projection
- Evaluate whether MCP `logging` should project into observability in addition to runtime events
- `.mcpb` / bundle host remains out of current product scope

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
- Keep terminal and Feishu host-management command routing aligned as new channels are added

### Orchestration

- Keep orchestration scoped to local/background task execution for terminal/TUI

## Remaining Conformance Work

- Expand conformance beyond the current first four cases
- Add golden coverage for MCP initialize/version negotiation and auth scope upgrade
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
