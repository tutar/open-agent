# TODO

## Phase 1: Object Model

- Done: canonical object models, schema envelope, stable enums, JSON-like serialization base
- Next: add richer `from_dict` parsing and capability origin metadata

## Phase 2: Tools + Session

- Done: `StaticToolRegistry`, `SimpleToolExecutor`, permission decisions, in-memory session state
- Next: add durable event log and richer policy/runtime filtering

## Phase 3: Harness

- Done: minimal `SimpleHarness` turn loop for `TUI-first`
- Next: add streaming model output, compact/recovery, and richer event pipeline

## Phase 4: Orchestration + Sandbox

- Done: in-memory task manager baseline
- Done: local sandbox capability and execution request/result contracts
- Add task record lifecycle and background-agent primitives
- Integrate policy and approval blocking into runtime flow

## Phase 5: Conformance

- Map unit and integration tests to `agent-sdk-spec/conformance`
- Add `basic-turn`, `tool-call-roundtrip`, `requires-action-approval`, and `session-resume`
- Add golden artifact replay helpers after core runtime exists

## Delivery Guardrails

- Keep GitHub CI required for merges to `main`
- Extend CI once integration tests and conformance suites exist
