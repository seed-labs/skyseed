"""Generate fixed display regions from Scene membership, not orbital positions."""
import math


def display_nodes(scene):
    nodes, orbits = {}, {}
    for satellite in scene.get_satellites():
        orbits.setdefault(satellite.orbit.group_id, []).append(satellite)
    radius = max(180, max((len(group) for group in orbits.values()), default=0) * 44 / (2 * math.pi))
    pitch = 2 * radius + 180
    columns = max(1, math.ceil(math.sqrt(len(orbits))))
    rows = max(1, math.ceil(len(orbits) / columns))
    for plane, satellites in enumerate(orbits.values()):
        for index, satellite in enumerate(satellites):
            orbit = satellite.orbit
            angle = 2 * math.pi * index / len(satellites)
            nodes[satellite.node_id] = {
                "name": satellite.name, "role": "Satellite", "group": orbit.group_id,
                "membership": f"{orbit.shell.constellation.group_id} / {orbit.shell.group_id} / {orbit.group_id}",
                "slot": index + 1,
                "x": plane % columns * pitch + radius * math.cos(angle),
                "y": plane // columns * pitch + radius * math.sin(angle),
            }
    left, top = -radius, -radius
    right, bottom = (columns - 1) * pitch + radius, (rows - 1) * pitch + radius
    stations = scene.get_ground_stations()
    ground_columns = max(1, math.ceil(math.sqrt(len(stations) / 2)))
    for index, station in enumerate(stations):
        nodes[station.node_id] = {
            "name": station.name, "role": "GS", "group": station.group_id,
            "location": f"{station.latitude_deg:.4f}°, {station.longitude_deg:.4f}°",
            "x": right + 280 + index % ground_columns * 200,
            "y": top + index // ground_columns * 130,
        }
    bottom = max(bottom, top + (math.ceil(len(stations) / ground_columns) - 1) * 130)
    layout = {
        "ut": {"x": left - 360, "y": (top + bottom) / 2},
        "zones": {"x": left + 120, "y": bottom + 280,
                  "columns": max(1, int((right - left) // 350)), "dx": 350, "dy": 160},
        "headings": [
            {"name": "UT", "x": left - 360, "y": top - 120},
            {"name": "Satellites", "x": (left + right) / 2, "y": top - 120},
            {"name": "GS", "x": right + 280 + (ground_columns - 1) * 100, "y": top - 120},
        ],
    }
    return nodes, layout
