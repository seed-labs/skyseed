# Static GroundStation Emulator and Minimal Ground Integration

## 1. Status and scope

This document records the implemented second Skyseed v2 vertical slice. It
adds one static GroundStation, one ordinary ground Router, one IX and one
explicit eBGP session while retaining the two isolated Satellites.

The slice does not implement GS–Satellite links, satellite-side interfaces,
visibility, connection selection, dynamic routing, UT, Simulator, Topology,
Planner, BPF or MPLS updates.

## 2. One GroundStation object

The object and contract boundary is:

```mermaid
classDiagram
    class GroundStation {
        <<implemented / evolving>>
        +ground_station_id
        +location
        +station_type
        +operator
        +service_config
        +runtime_reference
        +joinNetwork(network)
    }
    class SeedRouter {
        <<SEED>>
    }
    class GroundStationDescriptorProjector {
        <<implemented>>
        +project(ground_station)
    }
    class GroundStationDescriptor {
        <<immutable contract>>
        +ground_station_id
        +latitude_deg
        +longitude_deg
        +elevation_m
        +station_type
        +operator
        +runtime_node
    }

    GroundStation --|> SeedRouter
    GroundStationDescriptorProjector --> GroundStation : reads approved values
    GroundStationDescriptorProjector --> GroundStationDescriptor : produces
```

`GroundStation` in `src/skyseed_v2/domain/ground_station.py` is the only GS
object. `GroundStationAutonomousSystem.create_ground_station()` returns the
same instance later exposed by AS queries, registered as `rnode`, configured,
recognized as a border router, compiled and run. There is no
`GroundStationNode`, adapter object or binding map.

The static model contains stable ID, validated geodetic location, optional
elevation, station type, operator, display name and a copied read-only string
service configuration. It intentionally contains no visibility, service
satellite, Zone, TopologySlice, route-planning or runtime-update methods.

## 3. SEED Router lifecycle

`GroundStation` directly subclasses upstream `Router`. The custom AS owns the
instances because upstream `AutonomousSystem.createRouter()` has no public
node-class factory or insertion hook. Its narrow lifecycle extension:

1. registers each GroundStation as standard `(asn, "rnode", id)`;
2. lets inherited AS option propagation discover that `rnode`;
3. invokes the same `Node.configure()` used by upstream Routers;
4. registers the same object as `brdnode` after IX membership marks it as a
   border router.

Routing, Ebgp and Docker then discover the object through their standard
`rnode`/Router paths. Direct Router inheritance therefore proved sufficient
for this static slice without modifying upstream SEED or creating a parallel
route or Compose implementation.

## 4. Ground routing boundary

The formal example uses exactly one GS data-plane interface on `ix100`.
Standard Routing supplies IPv4 forwarding, Router image, BIRD, and `dummy0` as
the Router loopback. The GS has no local internal network and no satellite-side
interface.

The protocol controls are explicit:

- `GroundStationAutonomousSystem` sets upstream AS iBGP mode to `disabled`;
- the scenario also masks the GS ASN in `Ibgp`;
- `Ospf.maskAsn()` excludes the GS ASN;
- `Ebgp.addPrivatePeering()` creates one requested session to the ordinary
  ground AS.

Generated `/etc/bird/bird.conf` is the acceptance boundary: it has exactly one
`protocol bgp`, peer ASN 152, and no `protocol ospf` or iBGP session. No file is
deleted or modified after render to obtain that result.

## 5. Descriptor contract

`GroundStationDescriptor` in `src/skyseed_v2/contracts/ground_station.py` is a
frozen, slotted value object. The independent projector in
`src/skyseed_v2/projection.py` copies only approved scalar values and the
stable `RuntimeNodeReference(151, "rnode", "gs-new-york")`.

The Descriptor contains no GroundStation/Router, Registry, routing backend,
BIRD/FRR configuration, software, file, command, Docker/Compose object,
mutable interface or Emulator lifecycle method. Future Simulator needs must
extend this contract explicitly rather than expose the Router.

## 6. Compiler and metadata boundary

GroundStation is not handled by `_SatelliteCompilerBoundary`. It follows the
upstream `_compileNode()` Router path, including real network attachment.
`SatelliteDockerCompiler._getNodeMeta()` performs one small type check solely
to normalize GroundStation metadata by complete key:

- `role=GroundStation`;
- `type=rnode`;
- stable namespaced ID, location, classification and runtime reference.

The common keyed serializer replaces collisions before serialization, so no
duplicate YAML label key is emitted. This does not access
`_Docker__services`; that private member remains confined to the existing
Satellite no-network service append method. No GS dispatch, Compose path or
plugin framework was added.

GroundStation retains truthful upstream `net.0` metadata for its one IX
interface. Unlike isolated Satellites, it does not use `network_mode: none`.

## 7. Formal example

`examples/static_ground_station.py` builds:

```text
Satellite AS 65000
└── Shell / Orbit / two isolated Satellites

GroundStation AS 151
└── GroundStation gs-new-york ── ix100
                                  │ explicit eBGP
Ground AS 152                     │
└── standard Router ──────────────┘
    └── ground-net 10.152.0.0/24
```

The routable packet-level target is the ordinary Router's ground-side address
`10.152.0.254`. No static route stands in for eBGP.

## 8. Evolving and deferred decisions

Direct Router inheritance is accepted for the static slice because its real
costs—Router image, BIRD, forwarding and standard loopback—are exactly the
approved ground-routing capabilities. It is still an evolving boundary.
Future satellite-side dynamic interfaces, services, richer policy or multiple
GS roles require their own design and may expose additional SEED capability
trade-offs.

Upstream AS still lacks a public custom Router factory/insertion hook, so the
custom AS has a narrow lifecycle override. This is separate from the existing
Docker private-accumulator debt and does not require private AS-member access.
