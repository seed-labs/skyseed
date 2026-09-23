# Example scale and measured resources

Use Tiny for installation verification, S4 to learn default assembly, and S5 for a
smaller scene with local custom Models. S5 is a reasonable smaller starting point;
there is no verified 8 GiB support guarantee or established minimum RAM table.

These observations come from the [18 September closing campaign](../history/2026-09-18-closing-campaign.md)
and its retained `phase4b-2-closing-20260917T184610Z/campaign/` evidence. Its reference
host was Ubuntu 24.04.4, Linux 7.0.0-31, 32 logical CPUs and approximately 60.5 GiB RAM.
This was a shared desktop, with image caches and other work, not a hardware-capacity
benchmark. The [host guide](linux-host.md) separates required capabilities, optional
tuning and a later dated inspection of the workstation.

The maintained [native S4 example](../../examples/Sky06_s4_native_ospf/README.md)
uses 348 satellites, 355 GSs and 732 default Zones, with a 600-second initial range
and five-second observations. Its Linux forwarding, real per-satellite daemons and
explicit GS↔Zone Traffic have separate
[current validation notes](../../examples/Sky06_s4_native_ospf/README.md#validation)
and [prior fixed-pair evidence](../history/native-s4-experiments.md).
Per-satellite protocol files produce distinct build groups. Host neighbor capacity,
daemon convergence and kernel-operation delays affect execution. The interrupted
native S3 campaign is retained as historical evidence; it does not qualify S4.
The BPF measurements below retain their original routing and workload conditions.

## Scenes and concurrency

Each row used its full unchanged 600-second scene, five-second cadence and 121
formal/ACTIVE boundaries. Two deliveries covered 0–295 and 300–600. S4's separate
reset check covered only 0–30; it is not another complete 600-second measurement.

| Example | Shells / orbits / satellites | GSs / geographic Zones | Models and effective route concurrency | Runner workers / observed peak probes |
|---|---:|---:|---|---:|
| [S5](../../examples/Sky01_s5_custom_models/README.md) | 1 / 4 / 172 | 10 / 184 | Local current geometry, greedy ISL/GS; default routing used one compute worker, no process pool | 8 / 6 |
| [S4](../../examples/Sky02_s4_default/README.md) | 1 / 6 / 348 | 355 / 732 | Default Models; eight route workers | 8 / 8 |
| [S3](../../examples/Sky03_s3_ospf/README.md) | 1 / 36 / 720 | 355 / 732 | Example-local OSPF calculation in the owning process, no route pool | 8 / 6 |
| [Five Shells](../../examples/Sky05_five_shells/README.md) | 5 / 190 / 4,408 | 355 / 732 | Scoped default routing; five retained pools of eight, evaluated by scope; S5 jobs sometimes used one worker | 8 / 6 |

Five Shells has 3,660 scoped Zone-service identities over 732 geographic Zones.
GSs have ten shared slots, capped at two per Shell. Scoped paths do not cross Shells.
Retaining five route pools can retain 40 worker processes; it does not mean all five
scopes compute simultaneously. Worker counts come from recorded route statistics and
Runner snapshots, not merely requested CLI values. Deployer recorded startup
concurrency eight in all four cases; optional view workers are additional processes. More workers can increase memory
and contention as well as throughput.

## Stage timings

Seconds, rounded to one decimal. First material readiness measures calculation;
deployment and Runner preparation are separate operations. They are not additive
pieces of a universal runtime estimate. Views, retained waiting and control actions
also occupy wall time. Image caches and host load affect deployment substantially.

| Example | First material | Deploy command | Runner prepare, including B0 | Runner drain | Deployer cleanup | Case elapsed |
|---|---:|---:|---:|---:|---:|---:|
| S5 | 16.4 | 21.0 | 3.9 | 1.7 | 20.3 | 698.8 |
| S4 | 62.2 | 70.9 | 14.3 | 3.7 | 44.4 | 970.5 |
| S3 OSPF | 464.9 | 107.5 | 30.0 | 8.6 | 63.8 | 1,839.6 |
| Five Shells replay | 923.3¹ | 676.2 | 200.6 | 55.3 | 320.4 | 2,330.5¹ |

¹ First material is from the original calculation. The final replay reused retained
computed materials after the original computation process had closed; its case elapsed
time excludes that calculation. It is not a simultaneous computation-plus-execution
peak. S4 case elapsed includes pause/resume and the separate reset check.

Steady execution below uses `main_seconds` from the main invocation's
`activation_end` records, excluding preparation/B0: 120 updates per row. It measures
the update operation, not all deployment, preparation or packet delay. p95 uses
nearest-rank selection. Five Shells uses the corrected replay, S4 its full main run.

| Example | Update median / p95 / maximum (ms) |
|---|---:|
| S5 | 5.5 / 33.9 / 40.9 |
| S4 | 95.6 / 136.8 / 167.5 |
| S3 OSPF | 231.8 / 318.1 / 420.8 |
| Five Shells replay | 1,411.4 / 1,823.3 / 1,957.5 |

## Memory observations and limits

| Example | Initial / lowest sampled host available RAM (GiB) | Highest sampled host swap used (GiB) |
|---|---:|---:|
| S5 | 46.67 / 44.83 | 0.00 |
| S4 | 46.56 / 40.23 | 0.00 |
| S3 OSPF | 46.41 / 36.51 | 0.00 |
| Five Shells replay | 50.75 / 8.65 | 3.05 |

Available-memory sampling is not an isolated experiment peak. Sampling did not
continuously cover every calculation and deployment peak; initial-minus-minimum
available memory is only an approximate shared-host observation. **Total host RAM
minus minimum available RAM is not experiment memory consumption.** The replay
began with swap already used. The original five-shell attempt separately reached
4.57 GiB minimum sampled available RAM and 6.54 GiB maximum host swap use; those
measurements and its Traffic failure remain in the dated campaign.

Satellite count alone is insufficient for sizing. GS and Zone counts, candidate
search, chosen Models, routing demands, retained calculation/pools, optional views,
material reservations and worker concurrency all affect cost. Reduce scope explicitly
for your experiment and retain those conditions with any new measurement. The listed
S4/S5 examples keep their established teaching parameters.

## Network outcomes remain distinct

All four final main runs completed intended coverage and owned cleanup, with zero
infrastructure failures and telemetry ring loss. Their Traffic result was **WARN**:

| Example | Replies / requests | Missing-reply categories |
|---|---:|---|
| S5 | 1,471 / 1,472 | 1 unexplained reachable-path loss |
| S4 | 1,434 / 1,448 | 9 expected unreachable, 4 unexplained reachable-path losses, 1 unclassified |
| S3 | 1,086 / 1,459 | 363 expected unreachable, 2 unexplained reachable-path losses, 8 unclassified |
| Five Shells replay | 1,471 / 1,489 | 5 expected handover disturbances, 9 unexplained reachable-path losses, 4 unclassified |

These request categories retain the campaign's loss review; infrastructure completion
is not zero packet loss. Causes of the unexplained/unclassified windows remain unresolved.
The original slow-launch defect and successful five-shell replay remain valid evidence
for the paths observed. The later slow-stop Traffic regression adds coverage; it does
not retroactively invalidate the six-example campaign or turn it into a new run.
