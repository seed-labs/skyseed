# S4 Operational-Clean Example

Complete environment, host migration, and runtime requirements are documented
in `../../docs/operations/environment_and_running_EN.md`.

This directory is a self-contained, SEED-style composition root for the
validated Satellite Internet 1.0 scenario:

- planned S4 shell, 348 satellites;
- 28 selected UTs, 12 gateways, 14 zones;
- four 240-second horizons with 10-second sampling;
- strict `bpf_owned` shared G/Z forwarding;
- operational-clean rolling maintenance and nonblocking observation.

`experiment.py` is the single editable definition. It selects the shell,
ground preset, horizon, sampling, runtime, start time, and output directory.
Platform implementation remains outside the example.

S4 is the public constellation choice. Its 348-satellite count is derived from
the shell definition; it is not an independent scale knob. A future reduced
experiment must name an explicit balanced sampling policy instead of silently
truncating with `max_satellites`.

The primary entry is deliberately short:

```bash
python skyseed_v1/examples/s4_operational_clean/run.py
```

That command writes generated inputs and audit artifacts under this example's
`output/<run-id>/`. The terminal immediately acknowledges the launch and output
path, then emits a bounded heartbeat during the initial preview and coalesced
phase/epoch progress after the durable snapshot exists. Every long unchanged
phase continues to emit a bounded elapsed-time heartbeat with available stage
detail. This projection is a separate daemon observer: network updates never
wait for terminal output.

Cheap actions use the same file:

```bash
python skyseed_v1/examples/s4_operational_clean/run.py inspect
python skyseed_v1/examples/s4_operational_clean/run.py preflight
python skyseed_v1/examples/s4_operational_clean/run.py clean
python skyseed_v1/examples/s4_operational_clean/run.py preview
```

`inspect` and `preview` do not create the real network. `preflight` probes host
readiness without launching the experiment and prints a short human summary;
use `preflight --format json` only when full audit evidence is needed.
`clean` removes only scoped Skyseed runtime residue and then requires a ready
post-clean preflight; it refuses to run while a Skyseed process is active.

Changing `RuntimeConfig(sample_interval_seconds=10)` to `20` updates event
evaluation and visualization sampling automatically. The resolved configuration
is written to `output/<run-id>/effective_profile.json`.

The parameter-oriented CLI remains available for automation and unusual run
roots/IDs:

```bash
skyseed-v1 run \
  --example s4_operational_clean \
  --run-id inspect-s4 \
  --dry-run \
  --format text \
  --progress off
```
