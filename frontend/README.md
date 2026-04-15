# Frontend

- `terminal-tui/`: React + Ink + Yoga local terminal frontend that talks to the Python SDK through `Gateway`
- `desktop/`: reserved for the desktop frontend

Frontend code should not call harness internals directly. It should talk to the agent runtime through the gateway layer.
