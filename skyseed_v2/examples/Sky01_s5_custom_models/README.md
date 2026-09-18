# S5: author three local Models

[s5.py](s5.py) separately loads the shared [Shell](../../data/starlink/shells.yaml)
and [GroundStation](../../data/starlink/ground_stations.yaml) records, selects S5 and
filters GSs, then visibly constructs SEED Base, AS 65040, Shell, Orbits, Satellites
and GroundStations. It adapts pure input, declares time, loads Zones and injects
three instances from [model.py](model.py). Edit those algorithms directly;
there is no default Model subclass or cross-example construction import.

The frozen scene has **4 × 43 = 172 satellites**, 560 km altitude, 97.6° inclination,
RAAN 75.7°/87.7°/99.7°/117.7°, with unchanged 2015 epoch, IDs and input phases.
The independently loaded GS inventory supplies **six** stations in New Zealand
(latitude [-48,-34], longitude [165,179]) and **four** in southern Chile
(latitude [-55,-35], longitude [-76,-68]). A count discrepancy raises an error;
the entry never changes regions to find extra sites. Device limits remain four
ISLs and two GS accesses per satellite. The source GS capacities are unspecified.

## Authored algorithms and default roles

- `CurrentGeometryModel` evaluates current WGS84 line of sight and distance for
  requested ISLs, current GS elevation/range and Zone representative-point
  elevation. Its envelopes are 6,000 km ISL, 5°/2,000 km GS and 25° Zone elevation.
  It returns delay for the default parameter Model, with no future windows,
  prediction, plane classifications, search advice or work statistics.
- `GreedyInterSatelliteModel` considers all within-Shell pairs (14,706 for S5),
  retains feasible reservations, then fills remaining ports by shortest current
  distance with stable identity ties. It has no 2+2 plane target or global optimum.
- `GreedyGroundStationModel` retains feasible reservations then prefers higher
  elevation. Both endpoints' declared capacities apply; four is the local fallback
  only for an unspecified GS capacity. This greedy policy does not optimize
  station coverage or balance access across satellites.

Selected preparation reserves capacity. **Planner still requires five seconds**
for both link kinds; losing a candidate cancels its preparation. Bounded prehistory
can prepare persistent links before formal time zero. Current feasibility is not a
future-service guarantee. Defaults supply circular positions, Zone membership,
highest-elevation service, deadband parameters and gateway routing. PlanTransition derives event dependencies.
Unserved Zones and unreachable routes are ordinary results, not execution failures.
The [Model guide](../../docs/user-guides/model-extension.md) distinguishes necessary
value/identity/capacity contracts from optional optimization and Tool information.

## Author Zones once, then load them

From `skyseed_v2/`, reproduce the checked-in [zones.yaml](zones.yaml):

```bash
python tools/zone/generate.py --latitude-bands 12 --equatorial-cells 24 \
  --alignment antimeridian-centered --output examples/Sky01_s5_custom_models/zones.yaml
```

This produces **184 Zones**. Normal invocations load the file; they do not regenerate
it. `--check` in place of `--output` checks the exact generator result.

## Compute and browse

```bash
python examples/Sky01_s5_custom_models/s5.py
python examples/Sky01_s5_custom_models/s5.py --operation generate --duration-seconds 60
```

The initial Timeline starts at `2026-01-01T00:00:00Z`, spans 600 seconds and samples
at five seconds. Default `serve` computes this range, publishes views and waits.
Use `--through`, `--batch-seconds`, `--no-tools`, `--no-traffic`, `--output` and
`--override` as described in the [common guide](../../docs/user-guides/computation-and-controls.md).
`--execute` preserves explicit finite generation followed by `run_generated_experiment`,
including owned deployment cleanup. `--operation run` selects managed retained execution;
cancellation drains Runner/Traffic and its owned deployment. Ordinary separate deployment uses
the generated root controls.

Normal output is `examples/Sky01_s5_custom_models/output/`; open `output/index.html`.
[Interactive S5 demonstration](../../docs/demos/s5/index.html) and
[original ME2 measurements](../../docs/history/me2-examples.md) describe historical records.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#s5): Full default 600-second Docker/BPF run: Traffic WARN (1,471/1,472 replies); cleanup complete.
The dated record gives exact coverage, loss classifications and reference conditions.
