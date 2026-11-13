# S4: regional outage and persistent connection

[中文](README.zh-CN.md) · [Main workflow](../README.md)

This example uses 348 S4 satellites, 32 geographically selected ground stations,
and 732 global zones. Build the scene, deployment and complete plans, then run a
repeating 600-second plan until Ctrl+C. A regional GS outage lasts from 180 to 240 seconds;
measurements follow one UT–GS TCP connection through service changes and recovery.


## Manual repetition

The [manual](../MANUAL.md) covers nc, captures, handover times and interface checks.
`repetition.py` shifts saved BPF plans forward in time, restoring only differences
from the final state to the start state at boundaries. Containers, identities, UT
and application processes remain. This is an artificial topology jump, not further
orbital propagation or P&C recomputation. `console.py` displays progress and binds
Ctrl+C to `Runner.stop()`; `run.py` explicitly chooses repetition.

Primary/backup means geographic preference. Paths for all three pairs are computed
without availability-based reselection or TCP-session migration between GS IPs.
A manual application must explicitly connect to the chosen backup address.

## Workflow

Use the single [main workflow](../README.md). This directory contains only the
UT, outage, Store hosting and observation helpers called by those entries.
All plans are calculated before Run; View independently reads saved artifacts.

## Configuration

| Item | Setting |
|---|---|
| Constellation | Parameter-generated S4, 6 × 58, retaining the source epoch |
| GS | North America 5, South America 5, Europe 9, Africa 1, Asia 7, Oceania 5; names in `scene.py` |
| Sources | `data/starlink/shells.yaml`, `ground_stations.yaml`; GS frozen on 2026-08-25, including historical/planned entries, not a current operating inventory |
| Zones | 732 global zones; complete partitioning does not guarantee service or reachability |
| Timing | 600 seconds; request/save batches every 60 seconds; Link/Route evaluate every 5 seconds and emit changes |
| Link setup | Default 5-second preparation |
| Routing | Default GZ, destination-to-upstream local labels, BPF push/swap/pop; no extra Delivery delay |
| Endpoints | Hangzhou UT (30.2741°N, 120.1551°E), located in `zone-b16-c033`; Yamaguchi primary, Akita and Hitachinaka backups |
| Traffic | One sequential TCP echo stream about every 0.2 seconds, without automatic reconnect |
| Perturbation | Yamaguchi (34.2171°N, 131.55566°E), radius 180 km; Western Europe (50.2°N, 1.5°E), radius 600 km; GS links interrupted during 180–240 seconds |

The GS sample controls experiment size; it is not statistically representative.
The direct-IP terminal and service-following adapter are described in
[UT](ut/README.md). TCP echo tests a persistent byte stream without a telnet server.

## Coverage, zones and the traffic path

GS access requires Earth visibility, elevation ≥5°, link selection and a
5-second setup period. S4 has six orbital planes at 560 km and 97.6° inclination;
global zones do not imply continuous coverage.



| Relationship | Default rule | Limit |
|---|---|---|
| Zone → associated GS | This example selects the nearest three (the model default remains one) by spherical center-to-station distance, breaking ties by node ID | No reachability guarantee or availability-based reselection |
| Zone → serving satellite | At each evaluation, highest center-point elevation ≥5°; no eligible satellite means no service | Does not guarantee coverage of every zone edge or provide a UT radio link budget |
| Experiment UT → attachment | Follow actually adopted zone egress rules with a temporary veth | Separate from GS perturbation; no independent UT wireless delay/loss model |

The UT selects its Zone from its location. Service selection still uses that Zone's centre (33.75°N, 117°E); automatic traffic uses primary GS Yamaguchi.
Green highlights its rule-derived path, including ISL and the GS access segment;
purple is temporary UT access; grey-blue shows other active links. Zone boundaries
and the selector expose associations, adopted egress rules and geometric values
without changing the traffic endpoints. Elevation/range use interpolated model
positions; attachment follows actual execution records.

## Packet walkthrough

The UT keeps application address `192.0.2.2`; associated GS applications use stable Compiler-assigned node addresses (query with `skynodes`).
There is no IP-in-IP, address translation or second IP header.

```text
UT: 192.0.2.2 → primary GS, original TCP/2323
  → access veth → serving satellite classifies the destination and pushes a label
  → satellite hops swap local labels → primary GS pops → GS application

GS reply: primary GS → 192.0.2.2
  → GS classifies the UT /32 onto its Zone's label path
  → satellite label swaps → current serving satellite pops
  → temporary IP route 192.0.2.2/32 via access veth → the same UT
```

`ut/routing.py:TerminalRoutingModel` extends default GZ routing with this one
classifier at each of the three associated GS, reusing the Zone's next hop and downstream label.
It does not add a network-wide UT route or change transit label rules. The entry
is generated, saved, compiled and published with the ordinary route plan. An
unreachable path does not trigger selection of another GS.

Handover changes the access veth and egress delivery, retaining the UT container,
application address and TCP socket. TCP connects/accepts once and validates
numbered echoes without reconnecting. This is plain TCP echo, without Telnet
negotiation or login. The client starts after the first actual attachment and
continues through handover without restarting.

Edges handle the original IP packet. Intermediate satellites look up local labels,
write the next receiver's label and redirect. BPF owns rule/port selection and label
writes/swaps; Linux TC MPLS actions insert/remove the label header. No routing
daemon or Linux MPLS FIB is used. An unbound exit drops traffic.
See [UT](ut/README.md) for configuration and packet format.

## Interpreting recovery

| Observation | Interpretation and evidence |
|---|---|
| TCP remains silent, then the original connection resumes | Repeated retransmission timeouts increase the wait; network restoration does not reset the sender's timer |
| Continuous sequence numbers across handovers, one connect/accept | Evidence for this run's persistent session, not loss-free or interruption-free delivery |
| A probe remains outstanding at measurement end | Retain pending status, not a successful echo; early timeouts/peer closure remain errors |

Retransmission backoff follows [RFC 6298 §5](https://www.rfc-editor.org/rfc/rfc6298.html#section-5).
Separate network and application recovery: compare veth restoration
with matching TCP sequence retransmissions in `ut.pcap` / `gs.pcap`.
The result page retains actual replies, pauses and errors. Application silence is
not Runner execution lag. Endpoint captures do not establish every satellite hop.

Each run retains its actual recovery times and frozen input. Endpoint PCAP is
saved only when `CAPTURE = True` in `run.py`; manual mode defaults to no capture.
Compare result-page measurements to execution receipts and available PCAP before attributing a
pause. Successful continuity does not imply identical recovery timing on every run.

## Regional outage model

[perturbation.py](perturbation.py) defines `RegionalGroundOutage`. The model owns
its interval, circular regions and state across batches. `simu.py` registers it with:

```python
link.set_perturbation_hook(RegionalGroundOutage)
```

Use `link.set_perturbation_hook(None)` to disable the outage. Base LinkPlan remains
unchanged, and Network still computes routes from that base. Only execution links
are modified, so preemptive rerouting does not hide the outage. ISLs and UT access
are unaffected. At the end, restore connections and latest properties that still
exist in the current base plan.

The Japanese circle covers Yamaguchi. See the exported `build.json` for Western European membership. Membership uses
spherical surface distance, not a map-projection circle. This is a fixed-radius
example, not an electromagnetic propagation model: antenna response, frequency,
terrain and receiver thresholds can change real interference boundaries. No SINR
is calculated, and UT radio failures are not simulated.

| Contract | Use |
|---|---|
| `perturb(plan)`, `self.layer.scene` | Process a plan and read the associated scene |
| `plan.initial_links`, `plan.operations` | Boundary state and ordered changes |
| `LinkOperation.apply(links)` | Apply establish, withdraw and update to working state |
| `LinkPlan(start, end, initial, operations)` | Return a continuous execution segment |
| `state_at(t)`, `states_at(times)`, `slice(start,end)` | Query or slice retained plans |

`outage_description()` exports the regions, interval and GS list for this example
only. It is not a general perturbation visualization API. Run freezes this metadata,
so later model edits cannot change the interpretation of existing results.

## Store, tools and replay

Scene, Physical, Link, Network and Compiler pages call the existing
`tools/observation/` inspectors. The [execution reader](../../../tools/observation/execution/README.md)
joins plans to actual receipts. This example's `observation/` keeps only fixed
traffic statistics, UT/circular-outage overlays and page assembly. Generated
pages remain under `output/views/` or `output/runs/<run>/views/`.

| Data | Purpose and lifetime |
|---|---|
| etcd via `Store` | Shared values, atomic batches, revisions and watches; Runner writes events live and measurements are archived after draining |
| etcd database and WAL | Server-managed persistence; retained when the service stops |
| `store.json` | Portable export at the end of a run |
| Execution/measurement JSONL | Append-only history; the UT follower reads execution JSONL |
| PCAP | Endpoint packet captures |
| `inputs/` | Frozen Scene, compiled plans, settings and drawing observations for this run |

`Store` encodes values as JSON bytes for etcd's API. Plan files and captures stay
as files. Run starts etcd 3.6.5 on a random localhost port and stops it after
archival; Runner records use `runner/events/`, measurements use `user/`.
This example does not use etcd for P&C scheduling or multi-host clock coordination.
Offline replay needs no running etcd. File ownership and output layout are in the
[main guide](../README.md).

The globe reconstructs links, labels and UT attachment from frozen instructions
and actual acknowledgements. The orange region and 180–240-second window describe
the configured outage; GS link counts, paths and restoration follow actual receipts.
Traffic plots independently show real TCP replies and the configured window.
Positions come from observations saved during calculation and frozen for the run; replay does not query a model. Path animation is a derivation from adopted
rules, not a per-hop packet capture or independent kernel-state readback.
