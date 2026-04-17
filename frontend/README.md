# Frontend

- `terminal-tui/`: React + Ink + Yoga local terminal frontend that talks to OpenAgent through `Gateway`

Frontend code should not call harness internals directly. It should talk to the agent runtime through the gateway layer.
