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

Every normal run automatically generates its own offline page at
`output/<run-id>/visualization/index.html` after all rolling-horizon inputs are
frozen and before Compose starts. Open it directly in a browser to rotate/zoom
Earth, scrub or play time, and
toggle satellites, UTs, gateways, zones, ISLs, canonical UT-to-zone-anchor
attachments, and logical UT-to-gateway policy. The page reads the exact frozen
snapshots and physical links consumed by that run; it performs no independent
orbit propagation or anchor selection. Time or attachment disagreement fails
the run before Compose.

While the real runtime is in its epoch loop, run this in a second terminal:

```bash
python skyseed_v1/examples/s4_operational_clean/demo_ping.py
```

It reads the example's immutable UT gateway policy and gateway service
addresses, prints each default/special rule and the current runtime slice, then
runs three pings per rule from that UT container. A recent observer record may
also annotate the dynamic serving satellite; it is not a prerequisite.
`demo_ping.py --watch` repeats the demonstration until Ctrl-C, and
`demo_ping.py --ut ut2014` selects a particular UT. The equivalent unified
action is `run.py demo-ping`. This demo sends probe traffic
but never blocks the network update process.

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
