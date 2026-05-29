
# Implementation Notes

## The Complete Code

The program `SatelliteController.py` contains all the code. The other python code shows how each step works, and they are already included in the `SatelliteController.py` program. Here is the entire flow:

1. Genereate satellite network topology 
2. Map satellite ID to zone ID (`h3.latlng_to_cell`)
3. Use Dijkstra's Engine (`nx.single_target_shortest_path`) to calculates the complete shortest path tree for every node
4. Calculate the MPLS table 



## Generate Satellite Network Topology 

It takes TLE data, calculates the positions of all nodes at a specific exact time, checks for line-of-sight visibility (accounting for the Earth's curvature block), and builds a NetworkX graph compatible with your MPLS module.

1. Dynamic Edge Weights: Instead of arbitrary delays, the weights are now calculated mathematically using $\text{Delay (ms)} = \frac{\text{Distance in km}}{300\,\text{km/ms}}$ (speed of light through space).
2. NetworkX Object Passing: The output object current_topology_graph is a native nx.Graph. It completely matches the format your MPLS code expects, meaning you can swap controller.graph directly with this output without converting data primitives.


## Calculating MPLS Tables

The Python implementation using NetworkX. It defines a network topology, uses Dijkstra's Algorithm to find the shortest paths, and generates the explicit MPLS forwarding tables (LFIB) for every node.

1. Graph Setup (networkx): The controller uses nx.Graph() to maintain the network topography map.
2. Dijkstra's Engine (nx.single_target_shortest_path): Instead of running standard step-by-step routing protocols on the satellites, the controller calculates the complete shortest path tree for every node instantly.
3. Label Allocation Loop: The controller simulates Downstream Label Distribution. It starts at the destination, allocates a label, and passes it backwards to the node's predecessors, creating the In-Label $\rightarrow$ Out-Label relationship.




