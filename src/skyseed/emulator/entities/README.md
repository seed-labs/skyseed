# Authored entities

`EmulatorShell` holds Orbits; each `EmulatorOrbit` holds immutable `OrbitParameters`
and Satellites. Orbit epoch/phase are independent of the simulation Timeline.
`EmulatorSatellite` is a SEED ExtensionNode; `EmulatorGroundStation` is a Router
with location and optional access capacity. Shell and Orbit group authored entities;
they do not represent processes or resource lifecycles.

```mermaid
classDiagram
    class EmulatorShell {
        shell_id
        orbits
        create_orbit()
    }
    class EmulatorOrbit {
        parameters : OrbitParameters
        satellites
        create_satellite()
        create_satellites()
    }
    class EmulatorSatellite {
        satellite_id
        mean_anomaly_deg
        maximum_simultaneous_inter_satellite_links
        max_simultaneous_ground_station_accesses
    }
    class EmulatorGroundStation {
        ground_station_id
        location : GroundStationLocation
        max_simultaneous_satellite_accesses
    }
    EmulatorShell "1" *-- "many" EmulatorOrbit
    EmulatorOrbit "1" *-- "many" EmulatorSatellite
```

The AS factory in [seedemu](../seedemu/README.md) enforces identity uniqueness.
[Independent inputs](../../../../docs/user-guides/inputs-and-scenes.md) shows both
Satellite and GroundStation construction; [developer reference](../../../../docs/development/component-boundaries.md)
defines identity/capability semantics.
