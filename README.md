# OpenAgent

OpenAgent is a local-first Python agent project organized around `agent-spec`.

It focuses on a unified Python host, multi-channel interaction, builtin tools, durable local state, and a codebase that stays close to the spec’s module boundaries without introducing unnecessary distributed complexity.

## Highlights

- Unified host model: one Python host owns `Gateway + runtime + session + durable_memory`, while `terminal` and `feishu` attach as channels
- Local-first agent runtime: file-backed sessions, durable memory, model I/O capture, and restart-safe local workflows
- Rich capability surface: builtin tools, policy-aware executor, skills, commands, and MCP compatibility
- Local multi-agent baseline: delegated worker identity, background delegation, viewed transcript, and task notification routing
- Real integrations: terminal TUI, Feishu channel, Firecrawl-backed `WebFetch / WebSearch`
- Explicit architecture: harness, session, durable memory, tools, sandbox, and object model remain explicit

## Quick Start

Run these commands from the repository root.

Start the host:

```bash
export OPENAGENT_WORKSPACE_ROOT=$PWD
uv run openagent-host
```

Then start the terminal TUI:

```bash
cd frontend/terminal-tui
npm install
npm run dev
```

For full setup, real model configuration, Feishu, and Firecrawl, see [Get Started](./docs/get-started.md).

## Architecture Snapshot

OpenAgent runs as a single Python host process. The host owns the runtime, gateway, session state, memory, and tool execution. Channels such as terminal TUI and Feishu feed inputs into the same host instead of creating separate runtimes.

At a high level:

- `harness`: turn runtime, provider integration, context assembly
- `harness/multi_agent`: delegated worker identity, routing, viewed transcript
- `session`: event log, replay, short-term memory, durable state
- `durable_memory`: layered durable recall, direct write / extract / dream consolidation, taxonomy-aware long-term memory
- `tools`: builtin tools, executor, policy, skills, MCP, command surfaces
- `gateway`: channel normalization, session binding, egress projection
- `observability`: trace spans, progress, runtime metrics, session-state signals
- `sandbox`: local execution boundaries
- `harness/task`: local background task and verifier baseline

## Why It Stands Out

- Built as an agent project, not a thin SDK shell
- Keeps model input/output as durable local data under `.openagent/data/model-io`
- Supports both interactive local workflows and chat-channel workflows on the same host
- Separates shipped features from proposals and future work in the docs structure

## Docs

- [Docs Home](./docs/README.md)
- [Get Started](./docs/get-started.md)
- [Features Directory](./docs/Features/README.md)
- [Feature Proposals](./docs/Proposals/README.md)
- [Developer Guide](./docs/developer-guide/README.md)
- [Contributing](./docs/developer-guide/contributing.md)

## Project Status

Current shipped baseline includes:

- unified host + gateway runtime
- terminal TUI and Feishu channel
- builtin tool baseline with pluggable web backends
- local multi-agent delegation baseline
- session, short-term memory, durable memory, and replay
- model I/O capture and observability baseline
- skills, commands, MCP, and local sandbox/task baseline

Planned and discussion-stage features are tracked under [docs/Proposals](./docs/Proposals/README.md).
