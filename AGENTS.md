# OpenAgent Development Rules

`open-agent` is a Python agent project.

All work in this repository should follow these rules by default.

## Project Identity

- Treat `open-agent` as an agent project, not as an SDK skeleton.
- Use `docs/developer-guide/internals/module-structure.md` as the source of truth for module placement and directory responsibility.

## Code Quality

- Write code that is easy to read and maintain.
- Follow standard code conventions for the language and module.
- Prefer clear naming and straightforward control flow over clever implementations.
- Add concise comments when code would otherwise be hard to understand.
- Keep comments high signal; do not add comments that restate obvious code.

## Implementation Standard

Every feature change should include all of the following:

1. Code implementation.
2. Test additions or test updates that cover the change.
3. Documentation updates under `docs/` for the affected feature or behavior.

Do not treat documentation updates as optional follow-up work.

## Module Placement

- Place code according to module responsibility, not file length.
- Follow the module split described in `docs/developer-guide/internals/module-structure.md`.
- Prefer keeping top-level modules as facades or shared seams.
- Put domain implementation in the correct module area such as:
  - `harness`
  - `session`
  - `tools`
  - `sandbox`
  - `gateway`
  - `host`

## Refactors And Additions

- Preserve public APIs unless there is a clear, intentional change.
- When internal structure changes, keep compatibility re-exports where appropriate.
- Update tests and docs together with any structural refactor.

## Documentation Discipline

- Keep `README.md` focused on project overview and highlights.
- Keep detailed behavior and implementation guidance in `docs/`.
- When adding or changing a feature, update the corresponding feature or developer guide document.
