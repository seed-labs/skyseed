# Examples

Examples are user-facing executable compositions, not CLI implementations or
test fixtures. Each example owns one editable `experiment.py`, a short
`run.py`, its README, and an ignored `output/` directory.

Current executable examples:

- `s4_operational_clean/`: accepted full-S4 structural-cutover baseline;
- `s5_operational_clean/`: full S5 with the same ground/runtime components.

Each directory exposes `run.py inspect`, `preflight`, `preview`, and a default
real `run`. Copy a whole example directory when starting a new experiment.
The resolver records `effective_profile.json` under `output/<run-id>/`.
