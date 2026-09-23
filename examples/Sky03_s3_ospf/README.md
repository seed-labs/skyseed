# S3: replace the Route Model

The Emulator joins all GSs to the ordinary [shared business LAN](../README.md).
Only ground-interface IPv4 connectivity is added; original Model/input/timing choices
and BPF default remain. Historical measurements use their original scene.

[s3_ospf.py](s3_ospf.py) selects the 720-satellite S3 scene from independently
loaded Shell and GroundStation inputs, constructs AS 65040 with `create_static_emulator`,
adds the GS LAN, then injects [ospf_model.py](ospf_model.py).
It preserves default physical and other planning Models. This is local route
computation, not live protocol participation. The separate [native S4](../Sky06_s4_native_ospf/README.md)
uses actual FRR/BIRD area 0 and Linux forwarding. Different policies and coverage
make these contrasting approaches, not equivalent algorithm/performance experiments.

```bash
python examples/Sky03_s3_ospf/s3_ospf.py
python examples/Sky03_s3_ospf/s3_ospf.py --operation generate --through planner --duration-seconds 60
```

Default initial duration is 600 seconds, with five-second observations. Shared
`--operation`, `--through`, `--batch-seconds`, `--no-tools`, `--output` and `--override`
options follow the [common controls guide](../../docs/user-guides/computation-and-controls.md).
After each requested range, views are refreshed while the original computation waits.
Normal output is `examples/Sky03_s3_ospf/output/`; open `output/index.html`.
The [Area demo](../../docs/demos/s3/planner/index.html) uses retained evidence.

## Fixed Area layout

RAAN/identity-sorted orbits form six consecutive groups of six. Each sixth full
orbit and satellites whose input-epoch mean anomaly is in `[0°, 72°)` form the
backbone: **240 satellites**. The other **80 satellites per group** are internal
to Areas 1–6. Membership binds to satellite identity at construction, not moving
positions. Input phases are staggered between orbits.

Potential default ISLs receive explicit Areas: backbone–backbone is Area 0;
ordinary-region links and their backbone access links use that region's Area.
The 15,120 potential interfaces cover cyclic nearest same-orbit neighbors and
all candidate pairs in adjacent orbits, including last-to-first. These configure
membership; they do not create links or change the default 2+2 ISL selection.
All 240 backbone satellites have configured ordinary interfaces and are ABRs;
actual available interfaces and exits vary with physical/lifecycle facts.

Edit the phase/group constants in [ospf_model.py](ospf_model.py) and `area_interfaces(...)` for a static
layout. `COST_METRIC="unit"` is the default; `"delay_us"` and positive directional
interface overrides use the existing Model contract. Default physical, connection,
Zone (732), and three-GS geographic demand settings are retained. The Timeline
starts at `2026-01-01T00:00:00Z`, spans 600 seconds and has 121 five-second boundaries.

## Meaning and limits

The [local Model contract](#ospf-computational-route-model)
defines intra-area preference, backbone inter-area routing, directed costs and
one deterministic forwarding next hop. Ordinary-area SPF may traverse ABRs while
preserving their own forwarding decisions. GSs remain terminals; Zone destinations
resolve to the independent service selection. There is no cross-Shell routing.
This is route computation, not live OSPF adjacency, flooding, convergence or ECMP.
Initial acquisition and physically unavailable access can leave demands unreachable.
A finite run establishes only the recorded interval, not perpetual connectivity. Current validation and pending review are in [CURRENT](../../docs/CURRENT.md).


## OSPF computational Route Model

`OspfRoutePlanningModel` implements the existing `RoutePlanningModel`, including
`shutdown(wait=...)`. Callers inject it with
`create_default_simulator(definition, route_model=...)`; the default remains
`GatewayHopCountRoutePlanningModel`. Both share the same private geographical
gateway ranking and demand-construction code. Explicit demand input also works.
No ownership, Plan schema, Compiler validation or Runner semantics change.

### Supported subset and endpoint conventions

The computational reference is [RFC 2328 Sections 3, 16.1 and 16.2](https://www.rfc-editor.org/rfc/rfc2328.html).
The following is a deliberately bounded, point-to-point graph interpretation,
not a protocol implementation:

- Each ISL belongs to one ordinary area at both ends. Area 0 is the backbone.
  A satellite can have interfaces in several areas; it is an ABR when those
  memberships span areas. GSs never count as routers or ABRs.
- Per-area reverse SPF uses positive directed interface costs. Intra-area
  reachability takes precedence over inter-area routes. Equal intra-area costs
  choose the smallest area number, then the smallest canonical next-hop NodeId;
  SPF ties within an area also choose the smallest next hop.
- Exact reachable destination/cost summaries at backbone satellites seed the
  backbone SPF. ABRs compute inter-area routes using Area 0 only. Internal
  ordinary-area routers compare their intra-area distance to each advertising
  ABR plus its summary cost. The SPF path to that ABR may pass through another
  ABR; border routers are not traversal barriers. Ordinary-area SPF choices are
  installed only at internal routers, preserving every ABR's own intra-area or
  backbone forwarding decision. ABRs do not select ordinary-area inter-area
  shortcuts around the backbone. No virtual link, summary aggregation, external
  route or special area type is implemented.
- Only current operational Connections enter SPF. Explicitly configured satellite
  interfaces retain their area membership across outages, so losing a configured
  backbone does not reclassify that ABR as an internal router. Unlisted links
  contribute their default area only while operational. Disconnected areas or
  backbone components produce ordinary unreachable outcomes; there is no global
  shortest-path fallback. Invalid identities, duplicate interface definitions,
  invalid areas/costs and cross-Shell/scope interface configuration raise errors.
- A Zone terminates at its current service satellite, with zero additional
  routing cost. At an ABR that terminal is visible in all its attached areas.
  An unserved Zone yields `ZONE_UNSERVED`.
- An explicitly requested Satellite destination uses that satellite as the terminal
  in the existing area calculation, without creating default Satellite demands.
- A GS destination is a terminal stub reached over each actual operational
  access. By default each access is advertised in all attached areas of its satellite
  (including all areas of an ABR), with the satellite→GS interface cost. An
  explicit access reserves a satellite-side terminal-stub interface in its
  assigned area; that satellite can therefore be an ABR, while the GS cannot.
  This membership survives outages just like an explicit ISL interface. A
  satellite with no configured/operational interfaces uses `default_area` for local
  terminals, if enabled. Several attachments advertise the same terminal;
  intra-area preference still applies. These are endpoint conventions, not GS
  protocol participation.
- A GS source selects the attachment with the least **actual complete path**
  cost after satellite forwarding choices have been fixed; ties use NodeId.
  GS→satellite cost and the final satellite→GS cost are included. GSs never
  transit a path or join isolated `network_scope` graphs or Shells.
- One next-hop function is selected for each scope/destination. Requested paths
  follow that function, including decisions at downstream ABRs. Intra-area cost
  decreases along intra-area choices; inter-area choices progress from an
  ordinary area to the backbone to intra-area forwarding. This yields loop-free,
  merge-consistent paths without ECMP, demand removal, or relaxed Compiler checks.
  At a downstream ABR, the actual path follows that router's own decision,
  which can differ from the SPF continuation toward the advertising ABR.
  Tool costs sum the actual path, not an inferred SPF metric.

No adjacency negotiation, LSA packets/flooding, protocol timers, convergence
simulation, routing daemon, service layer, cross-Shell routing or new backend is
included. Compilation cannot demonstrate protocol convergence or live forwarding.

### Configuration and reuse

```python
from examples.Sky03_s3_ospf.ospf_model import OspfRoutePlanningModel, OspfInterfaceConfiguration

route_model = OspfRoutePlanningModel(
    interfaces=(OspfInterfaceConfiguration(
        connection_id=actual_isl_id, area_id=1,
        cost_a_to_b=3, cost_b_to_a=7,
    ),),
    default_area=0,
    cost_metric="unit",
    gateway_candidate_count=3,
)
```

`OspfInterfaceConfiguration` is an immutable caller-owned value, defined locally in `ospf_model.py` along with the Model. It names a potential actual
Connection/interface pair, not an exclusive satellite label. Both directional
costs refer to **canonical** `connection_id.endpoint_a/endpoint_b`, regardless
of the original argument order used to create that ID. `area_id=None` explicitly
excludes that interface from OSPF. `default_area=None` excludes unlisted ISLs;
unlisted GS access still inherits its satellite's enabled memberships.

Costs default to one. Explicit overrides must be positive integers. Optional
`cost_metric="delay_us"` uses each direction's current effective `delay_us`,
floored at one, except where overridden; its metric is delay-weighted OSPF cost
in cost units. This is an experimental cost convention, not an RFC-mandated
mapping. Experiment provenance captures every effective constructor setting.

The Model retains one current graph per used scope and only currently requested
destination tables. Area adjacency, costs and ABR membership are shared; each
area/destination SPF is computed once per graph change. A cost change invalidates
tables even when topology is unchanged; irrelevant bandwidth/loss changes do not
invalidate unit-cost work. Changes/withdrawals compare against supplied previous
outcomes. Retained continuation uses these same bounded caches with suffix-only
formal calculation; shutdown clears them and prevents later computation.
The Model uses no worker pool and adds no scheduler or cache framework.


## ME2 offline reference (2026-09-17)

The historical ME2 run recorded all **121 boundaries and four Plans/realizations** for
600 seconds. Materials sealed in **908.170 s**; the complete entry including Tools
returned in **1,005.882 s**. The recorded interval has 1,440 operational ISLs,
714–719 served Zones, 3,803–3,963 reachable and 91–222 unreachable demand outcomes
per boundary. The final state has 1,989 operational connections. Area metadata
comes from the captured local Model; Tools neither import it nor rerun SPF.

These are offline facts, with no S3 deployment or live forwarding qualification.
Host: Intel i9-14900K, 32 logical CPUs, 64,933,851,136 bytes visible RAM, Ubuntu
24.04.4 LTS, kernel 7.0.0-31-generic; Conda `skyseed_dev`, Python 3.12.13, pip 26.1.2.
Generation overlapped offline validation and the smaller S5 generation; elapsed
values are measured references, not isolated benchmarks or hardware requirements.
Normal data/link and JavaScript interaction checks passed; no browser was opened
and visual acceptance remains with the user. Evidence and prior-output archival
mapping are in `skyseed-local/experiments/me2-20260917/`.

[18 September 2026 live campaign](../../docs/history/2026-09-18-closing-campaign.md#s3-ospf): full default 600-second Docker/BPF run with the local OSPF Model; Traffic WARN, cleanup complete.
The record separates planned unreachability from unexplained loss and infrastructure errors.
