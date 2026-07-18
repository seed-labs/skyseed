# Large Static Network

This is a pure v2 scale-validation input. Its local files describe one
6-orbit/348-Satellite Shell, 12 GroundStations, two ordinary ASes, two IXes and
two explicit eBGP peerings. The size was historically informed by an older
experiment, but neither `S4` nor v1 is part of the scenario's identity,
contract or runtime path.

Compile and validate without starting 365 services:

```bash
examples/large_static_network/start.sh --override
docker compose --parallel 16 \
  -p skyseed_v2_large_static_network \
  -f examples/large_static_network/output/docker-compose.yml config --quiet
docker compose --parallel 16 \
  -p skyseed_v2_large_static_network \
  -f examples/large_static_network/output/docker-compose.yml config --services \
  | wc -l
```

The expected service count is 365. A full build/up is intentionally not part
of the migration baseline. `operator: Starlink` is display metadata only and
does not select node type, ASN, routing or Compiler behavior.

Cleanup removes only this project's state, if present, and this example's
ignored output:

```bash
examples/large_static_network/cleanup.sh
```
