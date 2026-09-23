# Native S3 experiments — September 2026

These are historical experiments, not current run instructions or a convergence
qualification. The maintained [native S4 workflow](../../examples/Sky06_s4_native_ospf/README.md)
supersedes the old S3 entry. [Capability history](programmable-runtime-expansion.md)
records implementation decisions; [S4 history](native-s4-experiments.md) records the
explicit subsequent scope change, independently of whether S3 could succeed.

All roots below are under
`/home/lionheart/workspace/skyseed-local/implementation-baselines/`:

| Root | Conditions and retained evidence |
|---|---|
| `native-s3-completion-20260921T064026Z/` | Full S3 without Zones/scheduled Traffic; separate FRR and BIRD outputs, diagnostics and incoming source snapshots |
| `native-s3-zones-20260921T123847Z/` | Later GS↔Zone workload, small live checks and interrupted full FRR run |
| `native-s4-completion-20260921T144847Z/` | `s3-incident/`, `interrupted-s3-output/`, `s3-output-after-cleanup/`: previous-boot evidence and later cleanup, not S4 results |

## Full scene without Zones

The source used 720 satellites in 36 orbits, 355 GSs, ordinary ground LANs and
stable /32 identities, 600 seconds from 2026-01-01 00:00 UTC at five-second cadence
(121 frames). Retained calculation published two 300-second deliveries; startup
parallelism was eight. Each sequential deployment had 1,075 network nodes, Map and
two normally exited base-image services. There were no Zones or scheduled Traffic.
The historical result entry was `examples/Sky06_s3_native_ospf/output/index.html`.
Archived controls retain their original absolute paths; regenerate for a fresh run.

The host was Linux 7.0.0-31-generic/x86_64, 32 logical CPUs, about 60.5 GiB RAM,
Docker 29.6.2/Compose 5.3.1. Python was 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, with editable SkySEED/SEED
checkouts. Host settings/dependencies were unchanged. ISLs used 1 Gbit/s and
100 ppm loss; GS access used 500 Mbit/s and 200 ppm, with geometric delay.

Area-0 satellite OSPF excluded GSs. The ordinary `EdgeRoutes` Model supplied GS
access/uplink routes to exactly three selected satellite identities. FRR
8.4.4-1.1ubuntu6.7 used broadcast interfaces and 10/40-second hello/dead timers;
BIRD 2.17.2-cznic.1~noble used point-to-point and 1/4 at this stage. Different
configurations and sparse probes make these functional observations, not a benchmark.
No timers, geometry or loss settings were tuned to improve these results.

| Completed run | FRR | Final BIRD |
|---|---:|---:|
| Invocation | `20260921T073520.693817301Z-2663010` | `20260921T101509.144533989Z-3265836` |
| Deployment, seconds | 2679.260 | 2287.839 |
| Driver including survey/cleanup, seconds | 887.350 | 995.827 (invocation 994.187) |
| First-to-last activation, seconds | 757.773 | 643.224 |
| Maximum activation / completion lag, seconds | 169.457 / 309.915 | 44.587 / 43.198 |
| Completed activations | 121 | 121 |
| Satellites with 0/1/2/3/4 Full neighbors | 443/106/66/46/59 | 289/99/122/123/87 |

Both applied 4,006 link creations and 2,017 removals. The actual timeline had 1,440
initial ISLs, **no ISL removals**, 7,200 ISL parameter updates, 2,566 GS-access
creations, 2,017 removals and 13,760 parameter updates. The first GS changed access
set 25 times. There were 11,522 route actions and 1,989 final applied links. All
720 satellite positions changed; physical frames/candidates and Plans matched
between daemons. Small-scene ISL churn is separate evidence. A sampled delayed FRR
kernel stack showed `rtnl_dellink`, without establishing every delay's cause.

Seven probe groups requested stages 5/60/120/240/360/480/600; FRR's 240 group began
at actual frontier 245. Each pair used `ping -c 3 -i 0.3 -W 2` with stable sources.
Delayed execution could pass stages between probes, so these are neither continuous
loss measurements nor synchronized snapshots. Each table entry is replies out of 21:

| Probe | FRR | BIRD |
|---|---:|---:|
| Satellite 001/0001 → 001/0003 | 18 | 4 |
| Satellite 001/0001 → 019/0010 | 0 | 0 |
| First GS → 001/0001 | 15 | 6 |
| 001/0001 → first GS | 15 | 6 |
| First GS → 001/0003 | 12 | 0 |
| 001/0003 → first GS | 11 | 0 |

The satellites were `s3-orbit-001-satellite-0001` (`100.64.1.100`),
`s3-orbit-001-satellite-0003` (`100.64.1.102`) and
`s3-orbit-019-satellite-0010` (`100.64.2.213`). The first GS was
`starlink-gateway-00c7ca685b9e` (`100.64.0.1`, LAN `203.0.0.2/16`). It remained
attached and had exactly three protocol-253 destinations at all seven checkpoints;
not all loss can be assigned to GS detachment. All 720 daemon queries succeeded,
and all 355 GSs were daemon-free. Surveys used eight concurrent queries after the
endpoint and were non-atomic. The connected ISL graph placed the remote endpoint
25 graph hops away; FRR had no OSPF main-table routes there. BIRD had four active
Area-0 ISL interfaces but logged long I/O cycles, netlink drops, DBDES mismatches
and missing next hops. Neither run established full-constellation convergence or
reliable selected end-to-end communication.

## Map diagnostics and normal drainage

FRR passed HTTP/assets, full inventory and applied/conditional topology reads.
A terminal timeout followed by a successful command exposed an uncaught send to a
closing WebSocket and a backend restart. The separate SEED fix guards closure both
during Docker session creation and later data delivery. Final BIRD passed those
checks with the same Map container/Node PID throughout. Capture setup took 7.690 s,
clear 6.085 s. A stable-identity ping returned 0/8 with no valid packet records;
a direct observed ISL probe `172.16.4.86`→`172.16.4.87` (001/0001→001/0002), after
capture readiness, returned 8/8. The real parser accepted 32 records, 16 per capture
container. Cleared filters left no sampled tcpdump process. This establishes live
capture/framing/parser delivery, not OSPF transit or rendered activity.

CUA reported no enabled apps/browsers. These HTTP/WebSocket checks did not establish
visual usability, widget interaction, stable rendered positions or activity flashes.
The later browser evidence below has different conditions. After the final frontier,
both runs used runtime cancellation, calculation drainage and normal deployment
cleanup; Map became empty, sampled owned interfaces/protocol-253 routes were absent,
and exact-project container/network inventories were empty. No timeout shortened
these runs. Tiny retained separate Traffic generation/observation evidence; its BPF
live campaign was not repeated for the explicit-selector edits.

| Earlier failure or diagnostic | Finding, correction and limit |
|---|---|
| First FRR before B0 | Soft descriptor limit 1024 was insufficient. The helper now reserves `2 * node_count + 64` (2214 here), under unchanged hard limit 524288, before acquiring namespaces; inadequate hard limits fail early. Partial cleanup succeeded; retry used the same deployment. |
| First full BIRD, `20260921T085922.663823109Z-2999600` | All 121 activations completed, but per-node whole-inventory requests made one query take 733.930 s and capture setup span 1149.072 s until service shutdown. Only four probe groups and no final all-node survey were collected; it is diagnostic evidence, not final Map qualification. First/last 728.930 s, max activation 42.534 s, max lag 128.903 s; replies/12: nearby 3, remote 0, GS↔0001 9 each, GS↔0003 3/2. |
| Map setup cost | Full IDs now bypass repeated inventory; partial IDs still require unique resolution. At most eight Docker setup requests are admitted together, and accepted batches drain before reporting failure. The corrected harness separates Map checks from ping sampling. |
| Map stop | Old shell ignored TERM while waiting for Node. `docker stop --time -1` needed handled INT to shell/child, without SIGKILL. The wrapper now forwards TERM/INT and waits; a real stop completed in 0.094 s, exit 0. |
| Cancelled intermediate BIRD preparation | Accepted image build drained. Node opened 1075 descriptors in a real capacity check, disproving the proposed Map-shell limit fix; that speculative change was reverted. This is not a live BIRD run. |
| Docker Hub authentication EOF for `node:22-slim` | Deployment retry and direct pull reproduced the network error. `docker pull mirror.gcr.io/library/node:22-slim`, then `docker tag mirror.gcr.io/library/node:22-slim node:22-slim`, supplied matching base filesystem layers. Normal deployment resumed using completed groups; no Dockerfile/registry edit. Digest/layer comparison: `bird-map-base-mirror.json`. |

## Later S3 with Zones and Traffic

The next source retained all 720 satellites/355 GSs, ordinary ground LANs, default
Zones, 600 seconds and five-second cadence. Four explicit Arbuckle/Frankfurt and
California/Europe pairs produced eight directions; BPF S3 stayed unchanged. The
three-satellite policy became a fixture. Native workload format 3 (with v2 reads)
kept transit unknown; BPF retained strict complete-path selection.

Two five-node, 390-second runs completed 79 boundaries. Both verified same-/32
service C→A handover, unchanged H uplink preservation, G withdrawal/access move,
ISL withdrawal/recreation, Zone withdrawal/recovery, stable daemon PIDs and daemon-free
GSs. Two active Zone-source flows migrated only after completed Runner handover;
every flow replied in each stable window. FRR received 469/720 and BIRD 546/721,
including startup/deliberate outages, not a comparative protocol result. Normal
cleanup succeeded. The real Docker parser fixture took 14.235 s initially and
5.848 s unchanged; only the opaque ARG-dependent group rebuilt through Docker's
ordinary bounded path. Literal/heredoc reuse, shell `--from` and heredoc `FROM`
handling were verified without invented pulls.

Full FRR deployment took 1573.923 s using existing caches, with no prune/forced pull.
Runner completed 51 boundaries through 250 s, then stopped during activation of 255.
Actual `RTM_NEWNEIGH: No buffer space available`, incomplete ARP and growing
`table_fulls` prompted a live change from 128/512/1024 to 8192/16384/32768. Netlink
blocking and severe CPU/load pressure followed; systemd-oomd killed desktop processes
and the host rebooted at 22:09 CST. The exact cause remains unresolved; this is not
ordinary convergence or proof the capacity change caused the incident. Reboot restored
the original settings. Stopped project objects and the owned helper were removed before
S4. No completed full GS↔Zone S3 run or full BIRD run is claimed for this stage.

With CUA still unavailable, Firefox 154.0.1/geckodriver 0.37.1 checked offline preview,
timelines, Linux Compiler and sender replay. The actual FRR Map terminal, changing
edges, 47 packet rows and 96 flash samples worked; graph/terminal identities survived
updates and the narrow filter was cleared. These are mechanical checks, not user
visual acceptance. The prior normal output's 7,659 files were archived unchanged in
`incoming-native-output/`; no additional SEED source change occurred in this stage.

## Evidence index

| Root / files | Purpose |
|---|---|
| Full-scene root: `frr-output/`, `bird-output/`, `*-live.jsonl`, `*-summary.json`, `summarize-live.py` | Complete outputs, checkpoints, routes/qdiscs, daemon surveys, GS processes and reproducible summaries |
| `*-generation.log`, `*-deploy.log`, `*-run.log`, `*-cleanup.log`, `bird-final-generation.log` | Ordinary workflow commands and wall times |
| `*-map-check.json`, `bird-direct-map-check.json`, `bird-direct-packet-parser.json`, `bird-map-*-probe.json`, `bird-*-capture-after-clear.json` | Successful and failed transport/capture checks, identity and cleanup |
| `computed-timeline-summary.json`, `final-daemon-input-equivalence.json` | Actual dynamics and daemon-independent equivalence |
| `first-attempt-frr-*`, `frr-delay-kernel-stack.txt`, `bird-cancelled-preparation-output/`, `map-descriptor-capacity-check.json` | Descriptor failure, delayed kernel call and disproved hypothesis |
| `bird-before-session-fix-output/`, `bird-before-session-fix-*`, `bird-first-deploy-network-failure.*`, `bird-second-deploy-network-failure.*` | Diagnostic BIRD run and image-network failures |
| `map-full-capacity-tests-final.log`, `map-graceful-stop-test.json`, `environment.log`, `browser-availability.txt` | Map races/capacity/ES2015 TypeScript, real shutdown and environment limits |
| Zones root: `parser-live/`, `small-*-validation.json`, `small-*-live.jsonl`, daemon/raw Runner/Traffic logs, `browser/`, `browser-*.json` | Parser, small workload, actual browser and cleanup evidence |
| S4 root: `s3-incident/`, `interrupted-s3-output/`, `s3-output-after-cleanup/` | Previous-boot kernel/oomd logs, 720 daemon logs, 1078 container states and cleanup of the interrupted S3 result |

Full-scene focused runs passed 29 assembly/layout/LAN, 33 runtime, 65 route/boundary,
75 final workflow and 20 observation/Traffic/retained checks (three Node-dependent
skips); these overlap. Map race/capacity/TypeScript and real shutdown checks passed;
imports and 134-document checks passed. A material test's vacuous filter was corrected
to assert actual nonempty `route` actions. Incoming snapshots, separate SkySEED/SEED
task patches, final-file hashes and `preservation-checks.json` distinguish task work
from accumulated changes. Neither repository was staged or committed by these campaigns.
