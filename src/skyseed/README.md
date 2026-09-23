# Source organization

[Emulator](emulator/README.md) constructs the SEED scene. [Configuration](configuration/README.md)
loads declarations; [adapters](adapters/README.md) project them into receiver-owned inputs.
[Simulator](simulator/README.md) owns PhysicalWorld and Planner. [Backend](backend/README.md)
compiles Plans and executes retained materials. [Experiment](experiment/README.md)
orchestrates computation and controls, using [Deployer](deployment/README.md) for
network resources. [Shared](shared/README.md) contains neutral mechanics.

See [architecture](../../docs/architecture/overview.md) for relationships and
[developer reference](../../docs/development/README.md) for current contracts.
