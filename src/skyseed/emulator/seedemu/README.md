# SEED integration

`EmulatorAutonomousSystem` extends upstream `AutonomousSystem`. Its `create_shell()`
and `create_ground_station()` factories retain authored entities; `registerNodes()`
and `configure()` attach them to the SEED scene. Satellite ExtensionNodes participate
in the configured service topology; GroundStations remain Routers. The neutral
`NodeId` is bound once at this boundary.

`get_skyseed_autonomous_systems()` finds these ASs for the input adapter. Ordinary
SEED ASs may coexist. [Deployer](../../deployment/README.md) handles render/compile,
build/start and cleanup through its local adapter; this package does not patch the
separate upstream checkout. See [Tiny](../../../../examples/Sky00_tiny_network/README.md)
for a small mixed scene.
