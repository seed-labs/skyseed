# S5 Operational-Clean Example

Complete environment, host migration, and runtime requirements are documented
in `../../docs/operations/environment_and_running_EN.md`.

This example selects the complete S5 shell: 4 planes × 43 satellites = 172
satellites. It deliberately keeps the validated S4 example's other choices:

- 9 UTs, 5 gateways, and 6 zones qualified over a frozen two-hour window;
- four 240-second horizons;
- 10-second physical sampling and event evaluation;
- strict `bpf_owned` forwarding and `operational_clean` runtime;
- rolling process-worker horizon preparation and external zero-residue cleanup.

The S5 ground preset is independent of S4 runtime inputs. Its candidates are
evaluated over 720 ten-second frames starting at the declared experiment time;
only GW and UT locations served in every frame are retained, and S5-specific
zone bindings and UT gateway policies are then rebuilt. The two-hour window is
an explicit qualification range, not a claim that Earth-relative connectivity
is periodic forever. All user-editable choices are declared once in
`experiment.py`.

## Run

From the repository root, cheap checks are:

```bash
python skyseed_v1/examples/s5_operational_clean/run.py inspect
python skyseed_v1/examples/s5_operational_clean/run.py preflight
python skyseed_v1/examples/s5_operational_clean/run.py clean
python skyseed_v1/examples/s5_operational_clean/run.py preview
```

`preflight` prints a short human summary headed by `ready=True` or
`ready=False`. Use `preflight --format json` only for the complete audit
payload.
`clean` removes only scoped Skyseed runtime residue and then requires a ready
post-clean preflight; it refuses to run while a Skyseed process is active.

Every normal run automatically creates
`output/<run-id>/visualization/index.html` from that run's frozen H0-H3
snapshots and physical links before Compose starts. The standalone page
provides a rotatable 3D Earth and 96-frame timeline. It performs no independent
orbit propagation or anchor selection; source-time or attachment disagreement
fails closed before the real network is created.

Start the real run with:

```bash
python skyseed_v1/examples/s5_operational_clean/run.py
```

After the real run reaches its epoch loop, a second terminal can demonstrate
an actual selected path without manually finding containers or addresses:

```bash
python skyseed_v1/examples/s5_operational_clean/demo_ping.py
```

The action reads the example's immutable UT gateway rules and bound service
addresses, reports each default/special rule and the current slice, then sends
three pings per rule. A recent observer record can annotate the dynamic serving
satellite but is not required. Add `--watch` to repeat until Ctrl-C. The
equivalent unified action is `run.py demo-ping`. The probe
is external to, and never awaited by, the network update process.

The run writes artifacts under this example's `output/<run-id>/`. The terminal
immediately acknowledges the launch and output path, then emits a bounded
heartbeat during the initial preview and coalesced phase changes plus one line
for each completed runtime epoch. Every long unchanged phase continues to emit
a bounded elapsed-time heartbeat with available stage detail. Console
projection is a separate daemon observer and cannot delay network updates.
Delete `output/` after cleanup to clear all generated state for this example.

## Cost shape

S5 reduces expected Compose containers from S4's 416 to about 195. It is not
uniformly smaller: its near-polar geometry produces 399 distinct frozen
cross-plane candidate slots over the same 960-second window, versus S4's 190.
The smaller S5 ground preset reduces the declared dummy-network inventory to
605, versus S4's 631. Expect
lower container build/start cost but do not assume every preparation or cleanup
phase scales directly with satellite count.

The committed preset is qualified for at most 7200 seconds from its declared
start time, so `horizon_count=30` is the current maximum at 240 seconds per
horizon. A longer or differently dated experiment must rebuild/requalify the
ground preset. Fixed `loop_count` is still required; indefinite Ctrl-C-driven
operation is not currently an example capability.

There is no source `profile.json` or scenario expectation file. The common
resolver writes the actual `effective_profile.json` into each run directory.
