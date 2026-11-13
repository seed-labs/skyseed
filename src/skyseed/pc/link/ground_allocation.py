"""Exact residual GS allocation, used only for new connections.

Retained connections have already consumed capacity. The sparse flow graph
maximizes additional GS coverage, satellite coverage, edge count, then elevation.
It has one arc per actual candidate, never a full GS-by-satellite table.
"""
from heapq import heappop, heappush


def allocate_ground_links(edges, ground_capacity, satellite_capacity,
                          covered_ground, covered_satellites):
    """edges contains (GS, satellite, elevation, candidate); capacities are remaining."""
    edges = sorted(edges, key=lambda edge: (edge[0], edge[1]))
    if not edges:
        return ()
    grounds = sorted({edge[0] for edge in edges})
    satellites = sorted({edge[1] for edge in edges})
    ground_row = {node: index + 1 for index, node in enumerate(grounds)}
    satellite_row = {node: len(grounds) + index + 1 for index, node in enumerate(satellites)}
    source = 0
    sink = len(grounds) + len(satellites) + 1
    graph = [[] for _ in range(sink + 1)]

    def add_edge(start, end, capacity, cost):
        forward = [end, len(graph[end]), capacity, cost]
        reverse = [start, len(graph[start]), 0, -cost]
        graph[start].append(forward)
        graph[end].append(reverse)
        return forward

    # Integer weights bound every lower-priority objective, avoiding rounding
    # ambiguities in the shortest-path comparisons. Elevation resolves to 1 µdeg.
    edge_weight = len(edges) * 180_000_000 + 1
    satellite_weight = (len(edges) + 1) * edge_weight
    ground_weight = (len(satellites) + 1) * satellite_weight
    for ground in grounds:
        capacity = ground_capacity[ground]
        if capacity:
            reward = 0 if ground in covered_ground else ground_weight
            add_edge(source, ground_row[ground], 1, -reward)
            if capacity > 1:
                add_edge(source, ground_row[ground], capacity - 1, 0)
    pair_arcs = []
    for ground, satellite, elevation, candidate in edges:
        quality = round((elevation + 90) * 1_000_000)
        arc = add_edge(ground_row[ground], satellite_row[satellite], 1, -edge_weight - quality)
        pair_arcs.append((arc, candidate))
    for satellite in satellites:
        capacity = satellite_capacity[satellite]
        if capacity:
            reward = 0 if satellite in covered_satellites else satellite_weight
            add_edge(satellite_row[satellite], sink, 1, -reward)
            if capacity > 1:
                add_edge(satellite_row[satellite], sink, capacity - 1, 0)

    # Initial graph is a forward DAG. These feasible shortest-path potentials
    # make subsequent residual edge costs nonnegative for Dijkstra.
    potential = [0] * len(graph)
    for node in range(len(graph)):
        for target, _, capacity, cost in graph[node]:
            if capacity:
                potential[target] = min(potential[target], potential[node] + cost)
    while True:
        distance = [None] * len(graph)
        previous = [None] * len(graph)
        distance[source] = 0
        queue = [(0, source)]
        while queue:
            value, node = heappop(queue)
            if distance[node] != value:
                continue
            for index, (target, _, capacity, cost) in enumerate(graph[node]):
                if not capacity:
                    continue
                proposed = value + cost + potential[node] - potential[target]
                if distance[target] is None or proposed < distance[target]:
                    distance[target] = proposed
                    previous[target] = (node, index)
                    heappush(queue, (proposed, target))
        if distance[sink] is None or distance[sink] + potential[sink] - potential[source] >= 0:
            break
        for node, value in enumerate(distance):
            if value is not None:
                potential[node] += value
        node = sink
        while node != source:
            parent, index = previous[node]
            arc = graph[parent][index]
            arc[2] -= 1
            graph[node][arc[1]][2] += 1
            node = parent
    return tuple(candidate for arc, candidate in pair_arcs if arc[2] == 0)
