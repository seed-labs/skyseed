# Programmable-runtime expansion: design and implementation evidence

This record consolidates the 20–22 September 2026 capability changes and corrective
checks. [S3 experiments](native-s3-experiments.md) and [S4 experiments/acceptance](native-s4-experiments.md)
own live results and failures; [CURRENT](../CURRENT.md) states today's scope.
Historical names and paths below describe their original source conditions, not
current imports or runnable commands. Feedback is deferred to a subsequent conversation.

## Ownership and design decisions

The retained Emulator → static input → Simulator/Models → Plans → Compiler → sealed
materials → Runner flow was preserved. Ordinary node payloads use SEED software/files/
startup, with selection documented in the [payload guide](../user-guides/node-payloads.md).
No scheduler, protocol controller, cache service or additional runtime owner was introduced.

| Capability | Decision and reason |
|---|---|
| BPF customization | Separate definition/pure translation from Runner-owned binding. Standard C/ELF, declared structured data and node-level program selection replace a fixed packet handler while retaining shared completion/readers/telemetry. C/maps/headers belong to the [BPF contract](../development/dataplane.md). |
| Logical routing | Full paths and local source/target candidates, dispositions and immutable policy values coexist. Models need not complete paths or coverage. Satellite targets reuse node /32s and introduce no default all-pairs demands or destination-specific resources. |
| Fixed BPF local ingress | One owned veth pair per covered namespace routes local-source packets through the selected node program before network exit. It supersedes endpoint-specific selection and per-business-destination/access-peer Linux helpers. Network ingress shares the object/maps. PASS retains normal stack semantics without a delivery promise. |
| Native forwarding | One early BPF/Linux choice selects translation/realization. Linux uses ordinary IPv4 /31 links, ARP and ordered operations. Full paths or supported single-candidate local FORWARD/PASS/DROP lower explicitly; no inferred reverse routes, policy interpretation or reachability completion. |
| Shared identity | `NativeRoutingEnvironment` delegates existing deterministic allocation and interface naming, rather than reconstructing them in Examples. Authors construct it once from the complete scene node set; software preparation does not construct worlds or allocate identities. |
| Default assembly | An initial `experiment.create_simulator` wrapper was removed. `simulator.create_default_simulator(..., forwarding_backend="bpf")` alone selects BPF gateway or empty Linux routes when omitted. Explicit Models remain unchanged; mode enum/validation belongs to neutral configuration with existing backend exports retained. Definition/Planner have no execution-mode field. |
| Software adapter closeout | The initial `experiment.configure_native_routing` wrapper and later SEED `Routing.configureSoftware`/`validateSoftwareConfiguration` convenience methods proved unnecessary. The current `emulator.configure_native_software` validates before mutation and uses public Node package/file/base/startup methods. Only the SEED convenience additions, guards and their exclusive tests were removed; validation moved to SkySEED. General ExtensionNode and local Map work are preserved separately. |
| Protocol policy | Example templates own OSPF areas, interfaces, redistribution and edge routing. The adapter supplies optional dynamic-environment defaults, not protocol policy: BIRD's explicit 4-MiB kernel include and prefixed FRR inline nexthops, both author-overridable. |
| Preparation | `ExperimentController(preparation=..., preparation_inputs=...)` captures a module-level function and listed dependencies. Its detached context gives B0 runtime/planned/compiler facts, JSON input and captured paths. Existing readiness-process ownership runs it before Traffic/delivery/formal time, drains children on stop and preserves primary errors. No report format or deadline is required. |
| Captured package imports | Retain package roots and load captured dependencies/initializers, not changed originals; omitted initializers become namespace packages. Standalone functions remain supported. Captured closures/arbitrary objects are not supported; installed packages remain environment dependencies. |
| Tools | Development checks are opt-in clients; Traffic generation owns workload/senders through experiment orchestration; observation reads facts. Native sender evidence includes replies/migrations and planned context, never inferred transit. BPF hop replay remains separate. |
| Map | A bounded asynchronous observer publishes acknowledged link changes only; topology content changes independently of status/heartbeat. Gap/failure invalidates observation. No viewer I/O or container rescanning enters activation. The optional endpoint, dynamic-edge UI and capture/terminal fixes are local SEED Map changes, not guaranteed upstream. |
| Console | Existing owners emit flushed stderr transition messages and summaries before unchanged JSON stdout. Accepted cancellation is not completion; runtime resource cleanup, later Tools and deployment cleanup have separate results. Retained material exhaustion means waiting. Docker inherits terminal output for build/pull/start; inspections remain captured, client drainage/journals preserved. |

Current execution definition is v6 (existing v5 definitions without a hook remain
readable); material v8, BPF foundation `/3`, Linux foundation `/2`. Planning evidence
is v7, semantic state v4, Plan/transition v6; the fixed-ingress change uses C ABI 2 and
`skyseed-tc-ingress-v3`. Earlier stage material v5/v6 or native `/1` results must not be
relabelled as current. Exact contracts live in developer references, not this chronology.

## Image preparation and source-cost evidence

Deployer finishes prerequisites before bounded independent Compose batches. Content keys
include local context names/bytes/modes, resolved options, Dockerfile/platform and actual
base image IDs; immutable aliases isolate projects without a new cache owner. Named contexts
pin inputs. `pull` refreshes external inputs; `no_cache` forces the selected group. Private
journal v2 tracks an accepted batch; v1 reconciliation remains. Lost clients never prove
daemon completion; cancellation joins admitted work. The initial refusal of opaque inputs
was subsequently replaced by a bounded ordinary build path, without claiming complete reuse.

A six-group fixture (one prerequisite, four dependents, independent Map) used concurrency
two. Actual `RUN` timestamps measured maximum overlap two:

| Case | Compose build calls | Built groups | Wall s |
|---|---:|---|---:|
| Cold | 4 | all six | 31.878 |
| Unchanged project | 0 | none; same image IDs under new aliases | 2.137 |
| Map context change | 1 | Map only | 8.844 |
| External base change | 3 | prerequisite + four dependents | 13.976 |

This measures avoided invocations, not just layer-cache hits or 724-group build performance.
A later real parser fixture took 14.235 s initially and 5.848 s after regeneration;
only the opaque ARG-dependent group rebuilt. Shell `--from` text and heredoc `FROM` data
were not parsed as dependencies or pulled as invented images. Literal groups reused images.
Evidence: `native-correction-1-20260921T112902Z/build-final/` and
`native-s3-zones-20260921T123847Z/parser-live/`, below the external baseline root.

## Daemon compatibility decisions

Installed test packages were FRR `8.4.4-1.1ubuntu6.7` and BIRD `2.17.2-cznic.1~noble`.
These are source-specific corrections, not a universal protocol tuning recipe:

- BIRD forced device/kernel `scan time 1` was removed: notifications handle ordinary
  changes; package-default recovery scans remain. Timers changed from 1/4 to ordinary
  10/40, retaining point-to-point ISLs. [BIRD manual](https://bird.nic.cz/doc/bird-2.17.1.html).
- FRR uses supported interface `ip ospf passive` without mixing interface/network-area
  activation, all native allocation pools, broadcast discovery and default 10/40 timers.
  Custom SPF throttling was removed. [FRR 8.4 OSPF](https://docs.frrouting.org/en/stable-8.4/ospfd.html).
- FRR's accepted CLI `match metric 1` was absent from its active route-map; the
  [8.4.4 implementation](https://raw.githubusercontent.com/FRRouting/frr/frr-8.4.4/ospfd/ospf_routemap.c)
  registers no metric hook. Exact GS prefixes plus access-interface matches and implicit
  deny replaced that unsupported assumption; it is not exact protocol-253 filtering.
  Connected redistribution is limited to `sv2identity`, so removed service addresses
  actually withdraw. BIRD imports exact GS identities/protocol 253 and exports OSPF at metric 100.
- Later S4 checks motivated compact exact GS prefixes (`ge 32`), inline Zebra nexthops
  and a BIRD socket receive-capacity increase. Reasons, parser evidence, costs and remaining
  warnings are in [S4 evidence](native-s4-experiments.md#daemon-corrections-and-complete-runs).

The small final 390-second three-satellite/two-GS tests completed 79 boundaries for each
daemon: 28/28 forwarding/state expectations, 44/44 positive replies and 0/12 expected
unreachable replies. GS access removed/moved at 90/120, ISL removed/recreated at 180/210,
service /32 added/withdrawn/re-added at 30/150/300. Identities persisted, recreated ISLs
kept names with new ifindexes, daemon PIDs stayed fixed and GSs had no daemons. Cleanup
removed owned resources. No targeted deprecation/slow-loop/dropped-notification messages
were found. A preceding 300-second FRR attempt failed five expectations (four at 50 s,
one 45 s after ISL recreation); later checks passed. Final event/probe spacing allowed
ordinary convergence, without accelerating timers or claiming latency bounds. All attempts
remain in `native-correction-1-20260921T112902Z/qualified/` and its preceding-attempt evidence.

## Capability validation provenance

All paths in this table are under
`/home/lionheart/workspace/skyseed-local/`; each root retains original source baselines,
commands, raw results and task-only patches. Counts from selected runs may overlap.

| Stage / source root | Checks and limits |
|---|---|
| `experiments/dataplane-stage1-20260920T062642Z/` | Default translation/runtime split; finite 30-s Tiny and short three-delivery continuation without telemetry completed four ACTIVE boundaries and cleanup. Finite run left two terminal probe episodes unrun; not a full Traffic qualification. |
| `experiments/dataplane-stage2a-20260920T105135Z/` | Actual C/ELF compilation/linking, declarations, native helper simulations and material/runtime regressions; no kernel load/live/scale qualification. At that point structured data was deferred, later completed. |
| `stage2b-evidence/20260920T130458Z/` | Logical local routing, generated data and scoped definitions: 691 passed, 17 Node/privileged-opt-in skips; another 41 entry/deployment checks. Documentation 130, imports and whitespace passed. Offline evidence only. |
| `stage2b-correction-evidence/20260920T145620Z/` | Indexed demand/scope lookup, A→B→G threshold large-packet path, selected-instance pin accounting; 123 targeted checks including native C and pin calls. No live qualification. |
| `implementation-baselines/combined-3c3d-20260921T043936Z/` | Five-node FRR/BIRD 240-s checks: access withdrawal/move 60/90, ISL removal/recreation 150/180 and recovery by 230. FRR convergence seen by 50 but not 5 s; BIRD by 5 s under different timers, not a latency comparison. Map links 4→3→4→3→4→0. A preceding 10-s startup probe retained early losses; 30-s BIRD build/Map output was separate. |
| Same 3C/3D root and `implementation-baselines/combined-3c3d-review-20260921T061422Z/` | Tiny BPF reset `reset-9bf9101d91ad4440a3df3a0f8f7a9109` 145/145 and Linux 144/144; preceding BPF pause/reset at 20 s 107/107. Fixed local ingress appears in capture, while Map has only inter-node links. Empty cleaned state verified. Retained outputs re-rendered; `restoration.json` identifies originals, not new runs. |
| `implementation-baselines/native-correction-1-20260921T112902Z/` | 187 SkySEED checks, 30 SEED checks with one incoming OSPF assertion excluded; 135-document/import checks. All 7,659 prior S3 files preserved. Upstream `dead count 2` versus `dead 2` assertion reproduced on incoming source; whole SEED test collection also needed unavailable unrelated `web3`. No dependency installed to mask it. |
| `implementation-baselines/native-usability-20260922T043919Z/` | 231 regressions passed/8 Node skips; 81 additional focused executions and four final preparation tests. Captured sleep-only hook/cancellation and five-node real 0/5/10-s BIRD run (hook checked five identities then slept 2 s) passed normal cleanup. FRR/BIRD/BPF Firefox checks included 145 BPF exchanges and recorded FRR hold. |
| `implementation-baselines/native-final-corrections-20260922T052754Z/` | Defaults/package captures/console/control layout: 46 core checks; 254 passed/4 Node skips exposed a stale native inventory, corrected in 121-check regression. 43 console/readiness, 66 lifecycle and final 34 shutdown/architecture checks; ten fork-in-thread warnings retained. Stale export failure reproduced on incoming source. Documentation 139, imports and shell syntax passed. |
| `implementation-baselines/programmable-runtime-usability-20260922T065229Z/` | 191 focused, 152 compatibility/one Node skip, 16 terminal/architecture checks; documentation 140/imports/whitespace passed. Real child streams and pseudo-terminal detection, fake-Docker failure/reconciliation and five-node generated Map labels checked. Firefox widths 1500/760/600 passed on retained manual evidence. No new deployment/S4 run. |

Usability archives preserve 5,437 historical FRR output files unchanged. A later pre-manual
`run.sh` refresh changed only that script; 5,389 other files matched. The accepted user's
5,549-file output was then preserved without script/view regeneration. These counts refer
to different snapshots and must not be substituted for one another.

The native Example narrowed from dual FRR/BIRD policy to BIRD-only teaching; FRR fixture
coverage remains. Native views use planned geometry at completed ACTIVE boundaries,
recorded holds and source migrations, not continuous measured positions or inferred hops.
The initial package-capture bug and crowded controls were corrected without changing
Model/Runner semantics. Final human messages reuse existing events, not a progress system;
ordinary GS stop hooks/signals/grace periods remain intact despite long manual cleanup.

The closeout removes the unnecessary SEED convenience-method dependency and consolidates
references by owner. It adds no public method, material version, allocation, runtime scan
or wrapper layer. Independent research/papers are outside the proposed phase commit;
SEED work remains local and separate. The current adapter uses existing Node facilities,
while required ExtensionNode support and optional local Map adaptations remain explicit
[dependency conditions](../user-guides/getting-started.md#python-and-the-required-seed-source).
