"""Scene input records and static display geometry; no propagation or PC import."""
from copy import deepcopy
from math import cos, sin, radians, pi

DISPLAY_EARTH_RADIUS_M = 6378137


def scene_inputs(scene):
    groups = []
    for group in scene.groups:
        item = {'group_id': group.group_id, 'kind': group.kind,
                'parent_id': group.parent_id, 'data': deepcopy(group.data)}
        if group.kind == 'shell':
            item.update(altitude_m=group.altitude_m, inclination_deg=group.inclination_deg,
                        epoch=group.epoch.isoformat())
        elif group.kind == 'orbit':
            item['raan_deg'] = group.raan_deg
        groups.append(item)
    nodes = []
    for node in scene.nodes:
        source = node.position
        nodes.append({'node_id': node.node_id, 'name': node.name, 'kind': node.kind,
                      'group_id': node.group_id, 'data': deepcopy(node.data),
                      'position_source': {'kind': source.kind, 'data': source.data}})
    return {'groups': groups, 'nodes': nodes, 'data': deepcopy(scene.data)}


def _circle_point(elements, phase):
    # Rotate the input circle, without advancing phase or converting epochs.
    radius = DISPLAY_EARTH_RADIUS_M + elements['altitude_m']
    inclination = radians(elements['inclination_deg'])
    raan = radians(elements['raan_deg'])
    x, y = cos(phase), sin(phase)
    return (
        radius * (cos(raan) * x - sin(raan) * y * cos(inclination)),
        radius * (sin(raan) * x + cos(raan) * y * cos(inclination)),
        radius * y * sin(inclination),
    )


def static_geometry(inputs):
    """Initial circular geometry in input axes; ground map in geographic axes.

    These axes may share a schematic view, never a simultaneous ECEF snapshot.
    Unknown source kinds retain their input records but get no invented orbit.
    """
    groups = {group['group_id']: group for group in inputs['groups']}
    outlines = []
    satellites = {}
    ground = {}

    def outline(elements, *, orbit_id=None, node_id=None):
        points = [_circle_point(elements, index * 2 * pi / 96) for index in range(97)]
        outlines.append({'orbit_id': orbit_id, 'node_id': node_id, 'points_m': points})

    for group in inputs['groups']:
        if group['kind'] == 'orbit':
            elements = {**groups[group['parent_id']], 'raan_deg': group['raan_deg']}
            outline(elements, orbit_id=group['group_id'])
    for node in inputs['nodes']:
        source = node['position_source']
        elements = source['data']
        if node['kind'] == 'ground_station':
            latitude = radians(elements['latitude_deg'])
            longitude = radians(elements['longitude_deg'])
            # The geographical view uses the same display sphere as its coastline.
            radius = DISPLAY_EARTH_RADIUS_M + elements['elevation_m']
            ground[node['node_id']] = (
                radius * cos(latitude) * cos(longitude),
                radius * cos(latitude) * sin(longitude),
                radius * sin(latitude),
            )
        elif source['kind'] == 'circular-orbit/v1':
            phase = radians(elements['mean_anomaly_deg'])
            satellites[node['node_id']] = _circle_point(elements, phase)
            if node['group_id'] is None:
                outline(elements, node_id=node['node_id'])
    return {'orbits': outlines, 'satellites': satellites, 'ground': ground}
