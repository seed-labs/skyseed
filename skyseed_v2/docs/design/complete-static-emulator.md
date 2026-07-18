# Complete Static Emulator and Simulator Input

## 1. Status and scope

The complete handwritten static example is implemented. It combines two
isolated Satellites, one GroundStation, one ordinary seedemu AS/Network/Router,
one IX, explicit eBGP, immutable Simulator static input and Docker compilation.
It adds no GroundStation–Satellite link and no dynamic Simulator field.

## 2. Static input contract

`SimulatorStaticInput` contains only:

- `SatelliteSimulationInput` values;
- `GroundStationSimulationInput` values;
- a read-only index keyed by `SimulationNodeId`.

The input is frozen, sorted by complete logical identity and fails closed on
duplicates. It contains no Emulator entity, Registry, container, namespace,
Compose service, persistence object or execution target.

Construction is direct:

```python
simulator_input = SimulatorStaticInput.from_autonomous_systems(
    (skyseed_as, ordinary_as)
)
```

Ordinary AS objects are ignored because they expose no supported Skyseed
entity input. Adding entities after construction does not mutate the existing
snapshot.

## 3. Per-entity conversion

```python
satellite_input = SatelliteSimulationInput.from_satellite(satellite)
station_input = GroundStationSimulationInput.from_ground_station(station)
```

The immutable input types own this copying operation. There is no stateless
conversion class and no separately instantiated multi-AS builder.

## 4. Formal complete example

`examples/complete_static_emulator/main.py` directly shows:

1. `Emulator` and `SatelliteBase`;
2. `SkyseedAutonomousSystem`;
3. Shell → Orbit → two Satellites;
4. GroundStation construction and IX attachment;
5. ordinary AS → Network → Router and IX attachment;
6. explicit eBGP;
7. `SimulatorStaticInput.from_autonomous_systems()`;
8. render and `SkyseedDockerCompiler`.

It deliberately imports no YAML Loader or application service.

## 5. Identity and runtime separation

`SimulationNodeId` uses ASN, entity kind and stable entity ID. It is independent
of seedemu Registry types and Docker names. Compiler metadata comes from the
real entity and integration lifecycle, not from static Simulator input.

## 6. Deferred work

Simulator runtime state, positions, visibility, dynamic links, resource
resolution and execution planning remain unstarted.
