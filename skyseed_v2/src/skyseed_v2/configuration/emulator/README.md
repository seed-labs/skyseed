# Emulator configuration

`load_shell_configs(path)` and `load_ground_station_configs(path)` independently
read their YAML inputs into immutable declarations. Aggregate
`load_autonomous_system_configs(path)` remains available. Records describe
identities, orbital parameters, station coordinates and optional device limits;
they do not imply an execution policy.

[yaml](yaml/README.md) handles syntax and validation. The example builds
Emulator entities visibly from these records. [Dataset origins](../../../../data/README.md)
distinguish source facts from chosen capacities; the [input guide](../../../../docs/user-guides/inputs-and-scenes.md)
shows the construction path.
