# Skyseed v2 Engineering Rules

Read `README.md`, `docs/CURRENT.md` and
`docs/architecture/terminology.md` before changing architecture or public API.

- Obtain explicit user approval before adding a concept to the public API,
  domain diagrams, Descriptor/Catalog contracts or Simulator boundary.
- Classify every new class as domain entity, value object, input
  specification, application service, operation result, integration adapter
  or internal helper. Record its owner, creator, consumers and why an existing
  type cannot carry the responsibility.
- Do not promote implementation convenience into a domain concept. Keep
  helpers and operation results module-internal by default; expose one only
  when a stable external consumer requires it.
- Give every public term one qualified meaning. Do not use bare ambiguous
  nouns such as Scenario, Catalog, Builder, Population, Result, Context,
  Manager, Coordinator or State without a documented qualifier.
- Keep examples traceable: a reader must reach the real public entity-creation
  method in a small number of explicit jumps. Do not hide duplicated or complex
  construction behind a facade.
- Update code, exports, docstrings, class/call diagrams, terminology, CURRENT,
  tests and validation records together whenever an architectural name or
  boundary changes.
- When design text and implementation differ, record the temporary design
  name, current formal name, reason for the change, whether responsibility is
  preserved and whether the difference is approved drift or a documentation
  correction.
- Do not start Simulator, dynamic topology, Runner or new cross-layer contract
  work without explicit authorization.

