# S4 manual experiment

[中文](README.zh-CN.md) · [Main workflow](../README.md)

This directory contains the temporary UT attachment, regional outage model,
plan repetition, console and offline-view navigation used by the S4 example.
After deployment, the network waits for the user's applications and captures.
See the [manual](../MANUAL.md) for ping, nc and interface observation.

## Default configuration

| Item | Setting |
|---|---|
| Constellation | S4, 6 planes × 58 satellites, 560 km and 97.6° inclination |
| GS | 32 geographically selected stations: North America 5, South America 5, Europe 9, Africa 1, Asia 7, Oceania 5 |
| Sources | `data/starlink/shells.yaml` and `ground_stations.yaml`; GS frozen on 2026-08-25, including historical/planned entries |
| Zones | 732 global zones |
| Timing | 600 seconds, saved in 60-second batches; Link/Route evaluation every 5 seconds |
| Links | Default 5-second setup period |
| Routing | GZ routing with local MPLS-like labels and no extra Delivery delay |
| UT | Hangzhou (30.2741°N, 120.1551°E), `zone-b16-c033`, address `192.0.2.2` |
| Associated GS | Yamaguchi, Akita and Hitachinaka, in geographic preference order |
| Outage | Yamaguchi circle (34.2171°N, 131.55566°E, 180 km) and Western Europe circle (50.2°N, 1.5°E, 600 km), during 180–240 seconds |

The geographic GS sample keeps the experiment at 32 stations.

## Access and handover

The UT's location selects its Zone. Service selection uses that Zone's centre
(33.75°N, 117°E): highest satellite elevation of at least 5°.
GS access also needs visibility, link selection and completed setup.
The nearest three GS are selected by spherical distance from the Zone centre. Applications choose which GS address to use.

[UT attachment](ut/README.md) follows actually adopted Zone egress rules.
It preserves the UT container and original IP packets while recreating the access
veth at handover. A GS classifier sends replies to the UT through the Zone's
existing label path. Applications and captures are started by the user.

## Repetition and console

`repetition.py` shifts saved BPF plans forward and restores only differences
between the final and initial network states. It retains containers and addresses.
The topology returns to its starting state every 600 seconds. Set `REPEAT = False` in `run.py` for one interval.

`console.py` displays phase, cycle time and current UT service. Ctrl+C calls
`Runner.stop()`; the owning contexts release UT, Runner and deployment resources.
Runtime status and adoption/attachment records remain for inspection.

## Regional outage model

`perturbation.py` defines `RegionalGroundOutage`. It owns its interval, circular
regions and state across batches. `simu.py` registers it with:

```python
link.set_perturbation_hook(RegionalGroundOutage)
```

Use `link.set_perturbation_hook(None)` to disable it. Base links and Network's
routing input remain unchanged; only execution GS links are interrupted.
ISLs and UT access are unaffected. Restoration uses links and properties still
present in the current base plan. Membership uses spherical surface distance.


`configuration.py` saves experiment settings and planned handover times.
`view.py` uses the existing observation Tools for saved Scene, Physical, Link,
Network and Compiler data.
