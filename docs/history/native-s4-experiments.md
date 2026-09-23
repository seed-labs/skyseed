# Native S4 experiments and acceptance — September 2026

S4 became the maintained native Example by explicit scope change after the
[interrupted S3 workload](native-s3-experiments.md), independently of whether S3 could
succeed. The campaigns below have different inputs and source conditions. Completion,
convergence, browser implementation checks and user acceptance are separate results.
Use the [current workflow](../../examples/Sky06_s4_native_ospf/README.md) for new runs;
[capability history](programmable-runtime-expansion.md) records the implementation changes.

Evidence roots are under `/home/lionheart/workspace/skyseed-local/implementation-baselines/`:

| Root | Role |
|---|---|
| `native-s4-completion-20260921T144847Z/` | Original fixed-pair sequential FRR/BIRD campaign, preceding S3 incident, configuration probes and source snapshots |
| `native-s4-acceptance-20260921T223333Z/` | Later near/far-pair campaign, FRR interruption and Map repair, separate BIRD output |
| `native-usability-20260922T043919Z/` | Historical FRR archive, replay projections, small captured-preparation live check |
| `native-final-corrections-20260922T052754Z/` | Interrupted-helper inspection/recovery and responsive browser checks |
| `programmable-runtime-usability-20260922T065229Z/` | Accepted manual-output hashes, separate Traffic replay, console checks and host administrator handoff |

## Original fixed-pair campaign

Source: six S4 orbits, 348 satellites, 355 GSs (703 network nodes), 732 default Zones,
600 seconds/five-second cadence, 121 boundaries and eight generated directions.
Anchorage/Frankfurt and Alaska `zone-b20-c002`/central Europe `zone-b18-c017` gave near
distances 356.3/266.6 km and far distances 7674.6/7132.0 km. Both Zones were served at
every boundary, each on seven owners with six changes. The satellite graph remained
one connected component; Anchorage had access at 121 boundaries, Frankfurt at 117.
These structural facts do not establish OSPF forwarding. GSs had no routing daemons;
native Traffic recorded unknown transit. The BPF S3 source was unchanged.

The environment was the existing `skyseed_dev`, Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`. Focused checks passed 261 tests
with four live/environment skips, then 13 exact-prefix integration checks; environment,
imports and 137-document validation passed. Base/layer caches were retained throughout.
External host sampling/guards used existing cancellation and verified campaign daemon
signals; they did not become runtime owners or install routes. Capacity was set before
deployment and restored only after owned work drained in this historical campaign.

### Protective stop and configuration costs

The first FRR deployment took 960.605 s at Compose parallelism four. The initial guard
stopped it after six five-second high-CPU samples: CPU PSI `some avg10` 76.47%, load1
235.31, available memory 46,805,385,216 bytes, zero swap/memory PSI/ARP table overflow.
Logs showed expensive initial configuration reading; they do not establish the cause
of the earlier S3 incident. Verified FRR processes exited normally after SIGTERM.
SIGINT to outer `run.sh` did not reliably cancel its Python child; the existing
`cancel-runtime` endpoint then stopped Runner at 275 simulated seconds. Normal cleanup
left no owned containers/networks. `frr-startup-stop/` preserves the whole interrupted
attempt; it is not a completed qualification.

An installed-FRR parser check fell from 9.441 to 5.918 s when adjacent GS identity
prefixes were collapsed into 11 entries constrained by `ge 32`, admitting exactly the
original 355 /32s. Redistribution still required a possible GS-access interface;
policy did not change. A diagnostic configuration reordering was rejected.
`frr-config-probe/` preserves commands/results. Read-only CLI checks on all 348 satellites
then distinguished configuration loading from convergence before ordinary `run.sh`.

The external guard was corrected to cancel independently of Runner progress. Finite
configuration-loading cost justified changing sustained CPU PSI >70% plus load1 >128
from six to 36 five-second samples (180 s). Less than 8 GiB available or memory PSI
>10% still tripped after two samples; any new kernel OOM kill tripped immediately.
OOM protection remained enabled. These were campaign guardrails, not capacity guarantees.

### Daemon corrections and complete runs

| Measurement | FRR before inline nexthops | Final FRR | BIRD before receive buffer | Final BIRD |
|---|---:|---:|---:|---:|
| Generation, seconds | 96.757 | 73.170 | 95.572 | 98.567 |
| Build/deploy, seconds | 933.634 | 970.585 | 952.547 | 964.737 |
| Compose build calls | 87 satellite batches; four other groups reused | 88: 87 satellite + Map | 87 satellite; four other groups reused | 87 satellite |
| CLI readiness, seconds | 67.493 + 10.162 retry | 67.321, four CLIs retried | 7.524, one CLI retried | 7.410, all first pass |
| Runtime plus detached Tools, seconds | 659.812 | 649.124 | 641.777 | 643.886 |
| Maximum non-initial boundary, seconds | 46.080 | 46.355 | 0.949 | 1.330 |
| Initial boundary, seconds | — | 0.918 | — | 1.097 |
| Replies / requests | 828 / 962 | 812 / 963 | 859 / 963 | 878 / 961 |
| Loss count / rate | 134 / 13.93% | 151 / 15.68% | 104 / 10.80% | 83 / 8.64% |
| Unchanged regeneration, seconds | 99.751 | 102.792 | 104.574 | 94.270 |
| Build-only reuse, seconds | 107.899 | 114.690 | 122.051 | 128.120 |
| Reused groups / Compose builds | 352 / 0 | 352 / 0 | 352 / 0 | 352 / 0 |
| Map packet rows / activity samples | 300 / 104 | 371 / 65 | 388 / 46 | 429 / 54 |

Each completed all 121 boundaries, all eight flows had replies, both Zones appeared on
all seven owners, and 24 source migrations/32 bindings were verified. Sampled daemon
PIDs stayed stable; all 355 GSs remained daemon-free. Runtime/deployment cleanup
completed. Final FRR and both BIRD runs checked 34 actual GS access changes. Final
FRR checked 84 GS kernel snapshots, BIRD 86 each, including four no-access withdrawals;
unchanged access retained its routes across Zone moves. FRR excluded 2/44 cross-boundary
Zone samples and final BIRD one cleanup-overlap sample using actual Runner events.
Nominal 600 seconds is not wall duration or a lossless interval.

FRR's first complete run logged 102 netlink-dp errors before `automatic_run_end` and
2492 afterward, including missing/invalid kernel nexthop IDs. ARP overflow was absent;
convergence alone cannot explain every loss. The installed parser accepted
`no zebra nexthop kernel enable`, supported by the
[FRR 8.4.4 implementation](https://raw.githubusercontent.com/FRRouting/frr/frr-8.4.4/zebra/zebra_vty.c).
This selects inline route nexthops during link replacement while preserving FRR
tracking/decisions. A five-node 390-second probe received 469/720, with every flow
replying in each stable window, no sampled nexthop objects/`nhid`, no targeted NHID
errors, and five remaining route-install errors after run end during cleanup.
`frr-nhg-probe/` and `frr-before-nhg-fix/` retain the probe and complete earlier outputs.
The pre-fix build-only check used CLI concurrency eight (no builds needed); deployment
used four. Later reuse measurements used four.

Final FRR had no targeted NHID error, but retained 2198 route errors (2194 missing
device, four invalid argument): 1053 before/in the end-event second and 1145 afterward.
It also retained 348 startup CPUHOG and 2180 starvation warnings. The non-atomic
540-second survey had 544 Full/DR, 546 Full/Backup, four Init, five 2-Way and one
Exchange; pre-fix counts were 543/545/four/six respectively. Packet-loss causality and
full-constellation convergence remain unproven. Two external wait scripts exited 143
for an unresolved reason; Runner returned zero, the observer continued, and later
checks restarted. That is not a claim the original harness succeeded.

BIRD's first complete run logged 2235 netlink notification drops during execution and
607 during cleanup, alongside 2178 unknown-interface, 1247 network-unreachable,
36 missing-device, 3081 OSPF-socket-unreachable and 2454 missing-broadcast warnings.
No I/O-loop delay was found. Native /31s were preserved without invented broadcasts.
Its survey had 1098 Full/PtP and four Init records.

The BIRD socket inherited 212992 bytes. The accepted `netlink rx buffer 4194304;`
setting, documented by the [2.17.2 manual](https://bird.nic.cz/doc/bird-2.17.2.html#krt-netlink-rx-buffer)
and [Linux implementation](https://raw.githubusercontent.com/CZ-NIC/bird/v2.17.2/sysdep/linux/netlink.c),
produced `ss` receive capacity 8388608; host `rmem_default=212992` and
`rmem_max=4194304` were unchanged. Thirteen checks took 12.23 s; the probe drained
normally. `bird-netlink-probe/` and `bird-before-netlink-buffer/` retain evidence.
Final BIRD's 65 sampled satellite checkpoints had the requested capacity/zero socket
drops; none of the 348 logs contained netlink-drop or buffer-setting errors. This
verifies the targeted fix, not a controlled packet-loss improvement.

Final BIRD still logged, during execution, 2198 unknown-interface, 2455 missing-broadcast,
1207 network-unreachable, 31 missing-device, 483 OSPF socket and 52 LSA-next-hop warnings;
cleanup added 2189 unknown-interface, 2285 network-unreachable, 728 missing-device,
17 OSPF socket and 318 LSA warnings. No configuration/buffer/I/O-loop errors appeared.
The non-atomic survey had 1097 Full/PtP and four Init records, not proof of convergence.

### Resources, browser and preservation

| Sampled host evidence | Final FRR | Pre-buffer BIRD | Final BIRD |
|---|---:|---:|---:|
| Five-second samples | 370 | — | 357 |
| Minimum available memory, GiB | 31.677 | 42.677 | 42.932 |
| Peak CPU PSI / load1 | 87.63% / 524.04 | 19.02% / 45.96 | 18.92% / 38.29 |
| Peak neighbor count | 3523 | — | 2471 |

FRR's longest extreme-CPU streak was 70 s, below the 180-second stop condition; memory
returned to about 51 GiB after cleanup. All completed runs had zero observed host/user
memory PSI, swap use, new OOM kills and ARP table overflows; guards did not trip.

Actual Firefox checked overview, PhysicalWorld/Planner/Compiler timelines, Zones,
Performance, eight-row preview and sender replay. Live Map retained graph/terminal
identity and dynamic edges; its first complete FRR graph had 704 nodes. A harness
iframe-body race was fixed, without product lifecycle changes. A separate SEED style
fix made packet logs opaque on white after a screenshot exposed graph/text overlap;
it changed no transport or forwarding. Final browser/Tools interactions passed.
Console vis-network physics-loop timeouts had timestamps insufficient to attribute them,
and a session-close response was truncated: `browser-console-limit.md` preserves
these warnings rather than claiming a clean console. Firefox 154.0.1/geckodriver
0.37.1 session DELETE completed and geckodriver exited zero.

This campaign temporarily raised neighbor thresholds from 128/512/1024 to
8192/16384/32768 before deployment, then restored them after the last owned work.
Ten recorded project IDs had no remaining containers; final logs showed no new
OOM/hung task/ARP overflow, and oomd remained enabled. This historical restoration
is not the later approved permanent-setting instruction. The original 7659 S3 files
and S3 incident stayed intact. `s4-task/` records 34 SkySEED files relative to its
incoming baseline, 44 with carried S3-Zones changes. The separate SEED task patch
contains the new style correction plus a separately identifiable incoming Map/software
baseline; those old software convenience methods were removed during closeout.

## Later near/far-pair campaign and interruption

The next campaign kept S4's 121 boundaries/600 seconds, sequential daemons, deployment
and runtime parallelism four, and two 300-second deliveries. Four relationships gave
eight directions per delivery (16 overall); near distances were 24.8–65.8 km and far
7128–7292 km. Greenville's Zone lacked a GS uplink before 285 seconds, with later
unserved periods; those losses must not be labelled convergence failures. Both runs
verified actual service/GS changes, 21 source migrations, Map/terminal/capture, Tools
and cleanup. The 540-second surveys were non-atomic (FRR Full/2-Way, BIRD Full/Init).

| Measurement | FRR | BIRD |
|---|---:|---:|
| Readiness, seconds | 123.38 | 38.95 |
| Replies / requests | 690 / 925 | 657 / 919 |
| Run wall time, including readiness/drain, excluding deploy/cleanup | 1643.77 s | 682.67 s |
| Recorded update hold | 867.12 s at 595 s | none |

FRR's hold supported a Map repair; senders stopped/rearmed while applications/container
clocks continued. The separate SEED fix handles split/coalesced responses and immediate
versus queued replies. Only Map refreshed; all 703 network container instances were
retained. FRR had invalid-gateway/interface-removal route warnings, BIRD network-unreachable,
unknown-interface and LSA warnings. Some coincided with updates/cleanup; absence of
matched FRR warnings during the static hold is not causal proof. Independent five-second
host samples reached minimum 24.27 GiB available, with transient CPU pressure but no
new OOM/ARP overflow; oomd remained active. `host-capacity.json` and `host-restoration.json`
retain this campaign's temporary settings.

BIRD invocation `20260922T000841.972495988Z-1757956` is retained in `bird-output/`.
The previous normal FRR output was later archived unchanged as 5437 files in
`native-usability-20260922T043919Z/historical-frr-output/`, verified by
`historical-frr-output-sha256.json`. These archive controls retain original absolute
paths and are for inspection, not fresh deployment. Retained replay reproduces both
reply totals, migrations and the FRR hold; BPF replay separately retains 145 exchanges.

The earlier FRR pre-formal-time interruption, invocation
`20260921T225933.237771592Z-1252667`, remains under `frr-b0-interruption/`. Its outer
exit was 143; the interruption cause remains unresolved. The stale `running` record
is preserved, not rewritten as successful cleanup. Other outer generation/observer
143 exits likewise did not prove drainage; producer sealing and resource absence were
checked separately.

The orphan `skyseed-linux-42ecbcc9db1e` had the exact interrupted foundation label.
Inspection in `native-final-corrections-20260922T052754Z/orphan-helper/` retained Docker
state/logs, original incident/Runner evidence, process inventory and source hashes.
Invocation/readiness PIDs 1252667/1253055 were absent; only idle shell/sleep remained,
with no command-channel process or bound scene containers. The existing
`_PrivilegedHelper.stop()` re-adopted that exact identity, rechecked its foundation
label and removed it. Final checks covered **all containers, standalone Runner
foundation labels/helper names and networks**, not only Compose labels. All 18115
image references retained IDs/tags/digests/creation times/sizes; a false alarm from
changing relative-age text was resolved by comparing stable fields. No cache was pruned.

## Accepted manual run and later implementation checks

The user subsequently completed generation, deployment, the full 600-second BIRD
run, cancellation and cleanup, and reported successful inter-satellite ping through
Map. This is accepted manual evidence from before the follow-up source edits:

```text
/home/lionheart/workspace/skyseed/skyseed_v2/examples/Sky06_s4_native_ospf/output
invocation 20260922T061416.080496065Z-2047246
```

All 5549 output files were byte-identical across the usability follow-up and are
preserved for closeout. No full S4 repeat or output regeneration accompanies the
console/documentation changes. The user's approximate satellite removal 43 s,
ordinary-service graceful stop 82 s and removal 32 s did not authorize changing
stop hooks, signals or grace periods. New Map identity descriptions require fresh
ordinary generation; accepted output was not edited to add them.

A separate `manual-traffic/` projection in the usability root reads 930 sends,
670 replies, 260 losses (27.9569%), 21 migrations and 38 finished sender segments,
with completed sender cleanup. It records endpoint delivery, not intermediate hops.
Firefox checked rotation, selection, lazy frame loading, playback/seek through 600
nominal seconds, focus/reset and responsive controls at 1500/760/600 widths. Spacing,
wrapping and no overlap/overflow were verified in real pages/screenshots. Earlier
`bird-traffic/` checks used the preceding BIRD invocation, not this manual run.
These are implementation checks, not renewed live/user acceptance.

The live neighbor settings later became the approved 8192/16384/32768. At handoff,
persistence was uncompleted because noninteractive sudo required a password. Exact
inspection, a three-key draft and administrator instructions remain outside the
repository in `programmable-runtime-usability-20260922T065229Z/`; current closeout
rechecks belong to its external review evidence. The [host guide](../user-guides/linux-host.md)
explains the general procedure without claiming this machine's persistence.

## Evidence navigation and limits

| Files within their campaign root | What they preserve |
|---|---|
| `full-{frr,bird}-validation.json`, `*-live.jsonl`, `*-daemon-logs/`, `*-gs-kernel-checks.json`, `*-log-review.json`, `full-bird-warning-timing.json` | Completed frontiers, actual routing/address checks, daemon and warning timing |
| `*-resource-summary.json`, `host-resources.jsonl`, `full-{frr,bird}-*-timing.json`, output `deployment/deployment-measurements.jsonl`, `{frr,bird}-reuse-output/`, `host-capacity.json`, `final-host-state.json`, current-boot logs | Wall-time/cache/resource observations and restoration under original conditions |
| `browser/`, `browser-*.json`/PNGs, `browser-console-limit.md` | Real page interactions, Map activity and unresolved console limitations |
| Acceptance root: `full-*-loss-assessment.json`, command records, `frr-map-pause-evidence.json`, `frr-b0-interruption/` | Later-pair source conditions, loss interpretation, long hold and unresolved interruption |
| Usability roots: `traffic-replay.json`, `*-refresh.json`, `browser-checks.json`, `live-validation.json`, `normal-output-validation.json`, `normal-output-refresh.json`, `final-ownership.json` | Exact replay inputs/hashes, small 0/5/10-second five-node captured-hook run, generated-only BIRD state, one historical `run.sh` refresh (other 5389 files unchanged), and later full manual-output preservation |

All roots retain incoming source/index/HEAD baselines and task-only patches separately
from accumulated changes. Validation counts and implementation limits are indexed in
[capability history](programmable-runtime-expansion.md). Native plots show planned
geometry at recorded completed ACTIVE frontiers and sender replies/migrations, not
continuous measured positions or packet hops. Daemon warnings, losses and the S3/FRR
interruption causes remain unresolved; none are erased by completion or manual acceptance.
