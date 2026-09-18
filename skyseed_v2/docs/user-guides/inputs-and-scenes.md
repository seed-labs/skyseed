# Construct entities and choose Zones

Emulator construction is upstream of physical simulation. You can declare SEED objects
directly, as in [S4](../../examples/Sky02_s4_default/s4.py), or pass validated configuration
to `create_static_emulator`, as in [Single Shell](../../examples/Sky04_single_shell_continuation/single_shell.py).
Both return a real upstream SEED Emulator.

## Satellite and GroundStation inputs

```python
from skyseed_v2 import configuration, emulator, adapters, simulator
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Paths are relative to this project root; authors can anchor them to __file__.
shells = configuration.emulator.load_shell_configs(Path("data/starlink/shells.yaml"))
stations = configuration.emulator.load_ground_station_configs(Path("data/starlink/ground_stations.yaml"))
s4 = tuple(shell for shell in shells if shell.shell_id == "S4")
network = emulator.create_static_emulator(
    configuration.emulator.StaticEmulatorConfig(skyseed_autonomous_systems=(
        configuration.emulator.SkyseedAutonomousSystemConfig(
            asn=65040, shells=s4, ground_stations=stations),
    )))
systems = emulator.seedemu.get_skyseed_autonomous_systems(network.getLayer("Base"))
start = datetime(2026, 1, 1, tzinfo=timezone.utc)
definition = simulator.SimulationDefinition(
    static_input=adapters.create_simulator_static_input(systems),
    timeline=simulator.SimulationTimeline(start, start + timedelta(seconds=600), timedelta(seconds=5)))
simulation = simulator.create_default_simulator(definition)
```

Satellite records retain orbital epoch/phase and explicit device capabilities. The Timeline
origin is separate from orbit epoch. GroundStation records retain geographic sites and optional
access limits; station labels or antenna counts do not prove commercial capacities. See
[data provenance](../../data/README.md) for sources, dates, transformations and assumptions.
Ordinary ASs, IXs, routing layers and peering stay explicit in the authored Emulator; Tiny
shows that construction. Simulator never reads those SEED objects back through a hidden bridge.

## Zones

Omitting Zone configuration selects the default generated global layout. Supply an authored
layout or explicitly disable it:

```python
settings = simulator.DefaultSimulatorConfiguration(
    zone_layout_definition=configuration.simulator.load_zone_layout_definition(Path("data/global_zones.yaml")))
# Explicitly disable: simulator.DefaultSimulatorConfiguration(zone_layout_definition=None)
simulation = simulator.create_default_simulator(definition, configuration=settings)
```

To generate a reusable file, use `python -m tools.zone.generate --help`. Generation arguments
choose latitude bands, equatorial cells and longitude alignment; the Tool writes
configuration, while configuration owns the layout algorithm. Membership answers geography;
a Zone's current serving satellite is a separate Planner result. The default 24-band,
48-equatorial-cell layout has 732 Zones. [Zone Tool](../../tools/zone/README.md) explains browsing.

Change one Model role at the assembly call using the [Model authoring guide](model-extension.md).
