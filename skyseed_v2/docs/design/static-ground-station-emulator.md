# Static GroundStation Emulator and Minimal Ground Integration

## 1. Status and scope

The static GroundStation slice is implemented. GroundStation participates in
ordinary seedemu Network/IX/eBGP behavior while Satellites remain explicitly
isolated. No GroundStation–Satellite link or dynamic Simulator behavior is
included.

## 2. One GroundStation object

`SkyseedAutonomousSystem.create_ground_station()` creates the unique
`GroundStation`. The same object inherits seedemu `Router`, is returned by AS
queries, joins Networks, registers as `rnode` and border router when applicable,
and compiles through the inherited standard Router path.

`GroundStationLocation` stays in `entities/ground_station.py` because it is
used only to construct and describe that entity.

```mermaid
classDiagram
    class SkyseedAutonomousSystem {
        <<entity owner + seedemu integration>>
        +create_ground_station(...) GroundStation
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
        +ground_station_id
        +location
        +station_type
        +operator
    }
    class GroundStationLocation {
        <<entity-bound value>>
        +latitude_deg
        +longitude_deg
        +elevation_m
    }

    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    GroundStation *-- GroundStationLocation : uses
```

## 3. Static Simulator input

`create_ground_station_simulation_input()` copies logical
identity, location and descriptive classification into a frozen
`GroundStationSimulationInput`.

```mermaid
flowchart LR
    GS[GroundStation] --> CONVERT[create_ground_station_simulation_input]
    CONVERT --> INPUT[GroundStationSimulationInput]
    INPUT --> ID[NodeId]
```

The input contains no Router reference, interface, Registry identity, border
state, container, Compose or execution-target data. Joining an IX and border
registration do not alter a previously created input snapshot.
GroundStation does not store `NodeId`; generation derives it from ASN,
ground-station kind and `ground_station_id`.

## 4. Routing and Compiler boundary

The complete example disables and masks OSPF/iBGP for the Skyseed AS and adds
one explicit eBGP peering to an ordinary ground AS. GroundStation uses the
ordinary Router Compiler path; Skyseed only normalizes metadata keys so the
logical entity remains recognizable.

## 5. Formal example

`examples/complete_static_emulator/main.py` directly shows GroundStation,
ordinary Router/Network, IX, explicit eBGP, static Simulator input and
compilation. It uses no YAML Config loading or static Emulator creation
operation; the static input is generated directly from `(skyseed_as,)`, with
no entity index or resolution path.

## 6. Deferred work

Dynamic satellite-facing interfaces, visibility, service selection, link
planning and Simulator execution remain unstarted.
