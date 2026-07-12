# Skyseed v1 Test Tiers

The normal human entry is:

```bash
python skyseed_v1/tests/run.py
```

Run one cheap tier with `python skyseed_v1/tests/run.py unit`, `contracts`, or
`integration`. CI and internal automation may equivalently use unittest
discovery; the short file is a convenience and visibility layer.

Tests follow the owner module instead of a historical experiment round:

- `unit/`: deterministic behavior of one module, with no Docker, host mutation,
  network access, or long-lived process;
- `contracts/`: serialized IR, fingerprints, backend ownership, frozen data,
  and fail-closed invariants;
- `integration/`: collaboration between package boundaries, CLI forwarding,
  examples, and SEED adapter imports;
- `replay/`: bounded consumers of committed or explicitly selected frozen run
  artifacts (added only when the fixture is small and stable);
- `system/`: real kernel, container, BPF, or host lifecycle checks; never part
  of the default cheap gate.

The 792 tests in the surrounding migration workspace remain an optional
historical regression suite; they are not required by a standalone v1
checkout. Tests for
retired executable routes are removed rather than kept as compatibility gates.
They are not copied wholesale because many encode old file paths or a specific
experiment round. A test moves when its implementation owner moves and its
assertion can be expressed as stable behavior. Stage 7 retires path assertions
and records any still-useful expensive/system cases explicitly.
