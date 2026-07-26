# Emulator configuration

This package owns every immutable Emulator declaration, schema constant, YAML
loader, and `StaticEmulatorConfigLoadError`. It creates no runtime entity and
contains no Simulator configuration.

## Local assembly / ownership map

Legend: `-->` is the ordered declaration-loading and construction handoff; it
does not make configuration the owner of the resulting Emulator graph.

```mermaid
flowchart LR
    SOURCE["YAML / handwritten declarations"] --> CONFIG["StaticEmulatorConfig"]
    CONFIG --> CREATE["create_static_emulator()"]
    CREATE --> GRAPH["upstream Emulator object graph"]
```

The other public declaration values expanded below are standalone inputs or
children of this aggregate: `EbgpPeeringConfig`, `EbgpRouterReference`,
`GeneratedOrbitLayout`, `GroundStationConfig`, `InOrbitPhaseDistribution`,
`InternetExchangeAttachment`, `InternetExchangeConfig`,
`LocalNetworkAttachment`, `NetworkConfig`, `OrbitConfig`,
`OrdinaryAutonomousSystemConfig`, `RightAscensionDistribution`,
`RouterConfig`, `SatelliteConfig`, `ShellConfig`,
`SkyseedAutonomousSystemConfig`, and `StaticEmulatorConfigLoadError`.

## Detailed public class/member diagrams

Legend: `A *-- B` means declaration A is composed of B declarations. Every
simple dataclass block lists all public fields.

```mermaid
classDiagram
    direction LR
    class StaticEmulatorConfig {
        <<complete immutable Emulator declaration>>
        +skyseed_autonomous_systems
        +internet_exchanges
        +ordinary_autonomous_systems
        +ebgp_peerings
    }
    class SkyseedAutonomousSystemConfig {
        <<immutable Skyseed AS declaration>>
        +asn
        +shells
        +ground_stations
    }
    class InternetExchangeConfig {
        <<immutable IX declaration>>
        +ix_id
        +prefix
        +route_server
    }
    class OrdinaryAutonomousSystemConfig {
        <<immutable ordinary AS declaration>>
        +asn
        +networks
        +routers
    }
    class EbgpPeeringConfig {
        <<immutable eBGP declaration>>
        +ix_id
        +peer_routers
        +relationship
        +sort_key
    }
    class StaticEmulatorConfigLoadError {
        <<Emulator YAML failure boundary>>
    }
    StaticEmulatorConfig "1" *-- "*" SkyseedAutonomousSystemConfig : Skyseed ASes
    StaticEmulatorConfig "1" *-- "*" InternetExchangeConfig : exchanges
    StaticEmulatorConfig "1" *-- "*" OrdinaryAutonomousSystemConfig : ordinary ASes
    StaticEmulatorConfig "1" *-- "*" EbgpPeeringConfig : peerings
```

Legend: `A *-- B` means Satellite declaration A contains B.

```mermaid
classDiagram
    direction LR
    class SkyseedAutonomousSystemConfig {
        <<immutable Skyseed AS declaration>>
    }
    class ShellConfig {
        <<immutable Shell declaration>>
        +shell_id
        +altitude_km
        +inclination_deg
        +epoch_utc
        +generated_orbit_layout
        +explicit_orbits
    }
    class GroundStationConfig {
        <<immutable GroundStation declaration>>
        +display_name
        +latitude_deg
        +longitude_deg
        +elevation_m
        +operator
        +station_type
        +ground_station_id
        +identity_key
        +ix_attachments
    }
    class GeneratedOrbitLayout {
        <<immutable generated layout declaration>>
        +orbit_count
        +satellites_per_orbit
        +right_ascension_distribution
        +in_orbit_phase_distribution
    }
    class OrbitConfig {
        <<immutable explicit Orbit declaration>>
        +orbit_id
        +right_ascension_deg
        +satellites
    }
    class SatelliteConfig {
        <<immutable Satellite declaration>>
        +satellite_id
        +index_in_orbit
        +mean_anomaly_deg
        +payload_items
    }
    SkyseedAutonomousSystemConfig "1" *-- "*" ShellConfig : shells
    SkyseedAutonomousSystemConfig "1" *-- "*" GroundStationConfig : stations
    ShellConfig "1" *-- "0..1" GeneratedOrbitLayout : generated layout
    ShellConfig "1" *-- "*" OrbitConfig : explicit orbits
    OrbitConfig "1" *-- "*" SatelliteConfig : satellites
```

Legend: `A *-- B` means generated layout A contains the complete distribution
values B.

```mermaid
classDiagram
    direction LR
    class GeneratedOrbitLayout {
        <<immutable generated layout declaration>>
    }
    class RightAscensionDistribution {
        <<immutable RAAN distribution>>
        +start_deg
        +step_deg
    }
    class InOrbitPhaseDistribution {
        <<immutable anomaly distribution>>
        +start_deg
        +step_deg
        +alternating_orbit_offset_deg
    }
    GeneratedOrbitLayout "1" *-- "1" RightAscensionDistribution : RAAN
    GeneratedOrbitLayout "1" *-- "1" InOrbitPhaseDistribution : phase
```

Legend: `A *-- B` means ground-network declaration A contains B.

```mermaid
classDiagram
    direction LR
    class OrdinaryAutonomousSystemConfig {
        <<immutable ordinary AS declaration>>
    }
    class NetworkConfig {
        <<immutable local-network declaration>>
        +network_id
        +prefix
    }
    class RouterConfig {
        <<immutable Router declaration>>
        +router_id
        +network_attachments
        +ix_attachments
    }
    class LocalNetworkAttachment {
        <<immutable local attachment>>
        +network_id
        +address
    }
    class InternetExchangeAttachment {
        <<immutable IX attachment>>
        +ix_id
        +address
    }
    class EbgpPeeringConfig {
        <<immutable eBGP declaration>>
    }
    class EbgpRouterReference {
        <<immutable eBGP Router reference>>
        +asn
        +router_id
    }
    OrdinaryAutonomousSystemConfig "1" *-- "*" NetworkConfig : networks
    OrdinaryAutonomousSystemConfig "1" *-- "*" RouterConfig : routers
    RouterConfig "1" *-- "*" LocalNetworkAttachment : local attachments
    RouterConfig "1" *-- "*" InternetExchangeAttachment : IX attachments
    EbgpPeeringConfig "1" *-- "2" EbgpRouterReference : peers
```

| Diagram class | Public contract | Required public members |
|---|---|---|
| `EbgpPeeringConfig` | `skyseed_v2.configuration.emulator.EbgpPeeringConfig` | `ix_id`, `peer_routers`, `relationship`, `sort_key` |
| `EbgpRouterReference` | `skyseed_v2.configuration.emulator.EbgpRouterReference` | `asn`, `router_id` |
| `GeneratedOrbitLayout` | `skyseed_v2.configuration.emulator.GeneratedOrbitLayout` | `orbit_count`, `satellites_per_orbit`, `right_ascension_distribution`, `in_orbit_phase_distribution` |
| `GroundStationConfig` | `skyseed_v2.configuration.emulator.GroundStationConfig` | `display_name`, `latitude_deg`, `longitude_deg`, `elevation_m`, `operator`, `station_type`, `ground_station_id`, `identity_key`, `ix_attachments` |
| `InOrbitPhaseDistribution` | `skyseed_v2.configuration.emulator.InOrbitPhaseDistribution` | `start_deg`, `step_deg`, `alternating_orbit_offset_deg` |
| `InternetExchangeAttachment` | `skyseed_v2.configuration.emulator.InternetExchangeAttachment` | `ix_id`, `address` |
| `InternetExchangeConfig` | `skyseed_v2.configuration.emulator.InternetExchangeConfig` | `ix_id`, `prefix`, `route_server` |
| `LocalNetworkAttachment` | `skyseed_v2.configuration.emulator.LocalNetworkAttachment` | `network_id`, `address` |
| `OrbitConfig` | `skyseed_v2.configuration.emulator.OrbitConfig` | `orbit_id`, `right_ascension_deg`, `satellites` |
| `OrdinaryAutonomousSystemConfig` | `skyseed_v2.configuration.emulator.OrdinaryAutonomousSystemConfig` | `asn`, `networks`, `routers` |
| `NetworkConfig` | `skyseed_v2.configuration.emulator.NetworkConfig` | `network_id`, `prefix` |
| `RouterConfig` | `skyseed_v2.configuration.emulator.RouterConfig` | `router_id`, `network_attachments`, `ix_attachments` |
| `RightAscensionDistribution` | `skyseed_v2.configuration.emulator.RightAscensionDistribution` | `start_deg`, `step_deg` |
| `SatelliteConfig` | `skyseed_v2.configuration.emulator.SatelliteConfig` | `satellite_id`, `index_in_orbit`, `mean_anomaly_deg`, `payload_items` |
| `ShellConfig` | `skyseed_v2.configuration.emulator.ShellConfig` | `shell_id`, `altitude_km`, `inclination_deg`, `epoch_utc`, `generated_orbit_layout`, `explicit_orbits` |
| `SkyseedAutonomousSystemConfig` | `skyseed_v2.configuration.emulator.SkyseedAutonomousSystemConfig` | `asn`, `shells`, `ground_stations` |
| `StaticEmulatorConfig` | `skyseed_v2.configuration.emulator.StaticEmulatorConfig` | `skyseed_autonomous_systems`, `internet_exchanges`, `ordinary_autonomous_systems`, `ebgp_peerings` |
| `StaticEmulatorConfigLoadError` | `skyseed_v2.configuration.emulator.StaticEmulatorConfigLoadError` | — |

## Entry points

Public declarations and `load_static_emulator_config()` are exported by
`__init__.py`; `yaml.py` owns YAML loading and
`StaticEmulatorConfigLoadError`.

## Dependencies

This package depends on validation/YAML primitives and neutral values. Emulator
construction consumes its declarations; it imports neither Simulator runtime
objects nor SEED integration state.

## Examples and tests

Complete, dynamic, and large examples use these declarations. Static
configuration/YAML, construction-equivalence, and negative loader tests cover
the package.

## Maintenance checklist

When declarations, YAML keys, or failures change, update these local maps and
detailed diagrams, configuration/construction/failure contracts, public
imports, fixtures, and focused tests.
