
# Design Notes


## Egress Points

In a modern satellite network architecture, there are two distinct types of egress (exit) points:

- Geographic Zones (H3 Cells): Where packets leave the satellite mesh to go down to a User Terminal (UT) via a localized satellite radio beam.

- Ground Stations (Gateways): Where packets leave the satellite mesh to go down to a physical teleport connected directly to the Terrestrial Internet (fiber backhaul).

To make your emulation accurate, the centralized controller must treat both as independent, routable endpoints in the graph. When a packet enters the satellite network, it will be tagged with a label corresponding to either a specific Target Zone (if it's destined for a user) or a specific Target Ground Station (if it's destined for a web server on the ground).


## Dividing Earth into Geographic Zones

To divide the Earth into geographic zones for a satellite network emulation, a common and highly efficient approach is to use a discrete global grid system.

The standard industry tool for this is Uber's H3 spatial index, which partitions the world into hexagonal cells. Hexagons are ideal for satellite emulation because every neighboring cell is an equal distance away, making it much easier to calculate satellite coverage footprints, handovers, and ground-station-to-satellite assignments.

If you prefer a lightweight approach without installing external libraries, you can partition the Earth into a standard grid matrix based on degrees.

### Starlink's Zone Shape

Starlink officially uses **hexagons**. Specifically, they rely on Uber’s open-source **H3 spatial index** to manage their ground territory, track capacity, and schedule satellite beams.

If you look at the official Starlink availability map on their website, the entire globe is partitioned into clickable hexagonal cells.

SpaceX chose hexagons over a traditional square grid for three major reasons:

* **Equidistant Neighbors:** In a square grid, a cell's diagonal neighbors are further away than its side neighbors. With hexagons, the distance from the center of a cell to the centers of all six of its neighbors is exactly the same. This makes calculating neighbor-to-neighbor handovers, routing, and interference modeling vastly simpler.
* **The Earth's Curvature:** Flattening a square grid over a sphere introduces severe distortion at the poles. Hexagons handle the Earth’s curvature with much less geographic distortion.
* **Dynamic Beam Tracking:** A single Starlink satellite uses phased-array antennas to project targeted "spot beams" down to the surface. Each satellite can cover thousands of **H3 size 5 cells** (each roughly 15 miles across) within its field of view. The symmetric nature of hexagons makes it much cleaner to mathematically map a circular radio beam to a ground zone.

Because you are using the H3 library in your code, your emulation approach is perfectly aligned with how Starlink handles its real-world ground zoning.