"""Own, construct, query and persist a satellite-network scene."""
from dataclasses import asdict
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime

from .scene_entities import (
    Constellation, GroundStation, GroundStationGroup, Satellite, SatelliteOrbit,
    SatelliteShell, ScenePositionSource, _number, _validate_data,
)
from .scene_endpoints import AccessEndpoint, default_zones
from .shell_layout import circular_shell_layout

_GROUP_TYPES = {entity.kind: entity for entity in (
    Constellation, SatelliteShell, SatelliteOrbit, GroundStationGroup,
)}
_NODE_TYPES = (Satellite, GroundStation)
_FORMAT = "skyseed.scene"
_VERSION = 2


class Scene:
    def __init__(self, *, data=None):
        self.data = {} if data is None else deepcopy(data)
        _validate_data(self.data, "Scene.data")
        self._nodes = {}
        self._groups = {}
        self._endpoints = {}

    @property
    def endpoints(self):
        return tuple(self._endpoints.values())

    def get_endpoint(self, endpoint_id):
        return self._endpoints[endpoint_id]

    def get_endpoints(self, kind=None):
        return tuple(endpoint for endpoint in self._endpoints.values()
                     if kind is None or endpoint.kind == kind)

    def add_endpoint(self, endpoint):
        if not isinstance(endpoint, AccessEndpoint):
            raise TypeError("endpoint must be an AccessEndpoint")
        if endpoint.endpoint_id in self._endpoints:
            raise ValueError(f"duplicate endpoint identity: {endpoint.endpoint_id}")
        self._endpoints[endpoint.endpoint_id] = endpoint
        return endpoint

    def create_endpoint(self, endpoint_id, *, kind, data=None):
        return self.add_endpoint(AccessEndpoint(endpoint_id, kind, {} if data is None else data))

    def create_zone(self, endpoint_id, *, latitude_deg, longitude_deg, bounds=None, data=None):
        fields = {} if data is None else dict(data)
        fields.update(latitude_deg=latitude_deg, longitude_deg=longitude_deg)
        if bounds is not None:
            fields["bounds"] = bounds
        return self.create_endpoint(endpoint_id, kind="geographic-zone/v1", data=fields)

    def create_default_zones(self):
        zones = default_zones()
        if any(zone.endpoint_id in self._endpoints for zone in zones):
            raise ValueError("default zone identities already exist")
        self._endpoints.update((zone.endpoint_id, zone) for zone in zones)
        return zones

    @property
    def nodes(self):
        return tuple(self._nodes.values())

    @property
    def groups(self):
        return tuple(self._groups.values())

    def get_node(self, node_id):
        return self._nodes[node_id]

    def get_group(self, group_id):
        return self._groups[group_id]

    def _group(self, group_id, expected_type):
        group = self.get_group(group_id)
        if not isinstance(group, expected_type):
            raise TypeError(f"{group_id} must identify a {expected_type.__name__}")
        return group

    def get_constellations(self):
        return tuple(group for group in self.groups if isinstance(group, Constellation))

    def get_shells(self, constellation=None):
        parent = None if constellation is None else self._group(constellation, Constellation)
        return tuple(group for group in self.groups if isinstance(group, SatelliteShell)
                     and (parent is None or group.constellation is parent))

    def get_orbits(self, shell=None):
        parent = None if shell is None else self._group(shell, SatelliteShell)
        return tuple(group for group in self.groups if isinstance(group, SatelliteOrbit)
                     and (parent is None or group.shell is parent))

    def get_satellites(self, orbit=None):
        parent = None if orbit is None else self._group(orbit, SatelliteOrbit)
        return tuple(node for node in self.nodes if isinstance(node, Satellite)
                     and (parent is None or node.orbit is parent))

    def get_ground_station_groups(self):
        return tuple(group for group in self.groups if isinstance(group, GroundStationGroup))

    def get_ground_stations(self, group=None):
        parent = None if group is None else self._group(group, GroundStationGroup)
        return tuple(node for node in self.nodes if isinstance(node, GroundStation)
                     and (parent is None or node.group is parent))

    def get_node_ids(self, *, groups=None, kind=None):
        """Union of selected groups and their descendants, in scene node order.

        None selects every group and ungrouped node. An empty selection selects
        no nodes. Unknown groups fail, including when the scene has no nodes.
        """
        if kind not in (None, "satellite", "ground_station"):
            raise ValueError("kind must be satellite or ground_station")
        selected = None
        if groups is not None:
            if isinstance(groups, str):
                raise TypeError("groups must be a collection of group IDs, not a string")
            selected = set(groups)
            for identity in selected:
                self.get_group(identity)
        result = []
        for node in self.nodes:
            if kind is not None and node.kind != kind:
                continue
            if selected is None:
                result.append(node.node_id)
                continue
            identity = node.group_id
            # The domain hierarchy has at most three group levels. No graph
            # copies or recursive full-tree scans are needed per selection.
            while identity is not None:
                if identity in selected:
                    result.append(node.node_id)
                    break
                identity = self._groups[identity].parent_id
        return result

    def _check_reference(self, entity):
        if isinstance(entity, SatelliteShell):
            parent = entity.constellation
        elif isinstance(entity, SatelliteOrbit):
            parent = entity.shell
        elif isinstance(entity, Satellite):
            parent = entity.orbit
        elif isinstance(entity, GroundStation):
            parent = entity.group
        else:
            parent = None
        if parent is not None and self._groups.get(parent.group_id) is not parent:
            raise ValueError(f"group {parent.group_id} is not the owning object in this Scene")

    def add_group(self, group):
        if type(group) not in _GROUP_TYPES.values():
            raise TypeError("group must be a supported Scene domain entity")
        self._check_reference(group)
        if group.group_id in self._groups:
            raise ValueError(f"duplicate group identity: {group.group_id}")
        self._groups[group.group_id] = group
        return group

    def add_node(self, node):
        if type(node) not in _NODE_TYPES:
            raise TypeError("node must be a Satellite or GroundStation")
        self._check_reference(node)
        if node.node_id in self._nodes:
            raise ValueError(f"duplicate node identity: {node.node_id}")
        self._nodes[node.node_id] = node
        return node

    def create_constellation(self, name, *, planes=None, satellites_per_plane=None,
                             altitude_km=None, inclination_deg=None, epoch=None, data=None):
        """Create an empty constellation or a uniformly spaced circular shell.

        Supplying geometry requires all five construction arguments. The empty
        form supports manually authored shells/orbits without a second API.
        """
        constellation = Constellation(group_id=name, data={} if data is None else data)
        if name in self._groups:
            raise ValueError(f"constellation identities already exist: {name}")
        parameters = (planes, satellites_per_plane, altitude_km, inclination_deg, epoch)
        if all(value is None for value in parameters):
            return self.add_group(constellation)
        if any(value is None for value in parameters):
            raise ValueError("provide all constellation geometry arguments, or none")
        pending = Scene()
        pending.add_group(constellation)
        shell = pending.create_shell(f"{name}/shell-0", constellation=name,
                                     altitude_km=altitude_km, inclination_deg=inclination_deg, epoch=epoch)
        for plane, (raan, phases) in enumerate(circular_shell_layout(planes, satellites_per_plane)):
            orbit = pending.create_orbit(f"{shell.group_id}/plane-{plane}",
                                         shell=shell.group_id, raan_deg=raan)
            for index, phase in enumerate(phases):
                pending.create_satellite(f"{orbit.group_id}/sat-{index}", orbit=orbit.group_id,
                                         mean_anomaly_deg=phase,
                                         name=f"{name}-p{plane}-s{index}")
        if self._groups.keys() & pending._groups.keys() or self._nodes.keys() & pending._nodes.keys():
            raise ValueError(f"constellation identities already exist: {name}")
        self._groups.update(pending._groups)
        self._nodes.update(pending._nodes)
        return constellation

    def create_shell(self, group_id, *, constellation, altitude_km, inclination_deg, epoch,
                     planes=None, satellites_per_plane=None, raan_start_deg=0,
                     phase_start_deg=0, phase_factor=0, orbit_raan_deg=None,
                     id_prefix=None, first_orbit=1, data=None):
        """Create a shell, optionally generating its circular orbits and satellites.

        Supply planes and satellites_per_plane to generate a regular layout.
        With neither count, create an empty shell for individually authored orbits.
        Numbering and id_prefix affect identity only, never orbital geometry.
        """
        _number(altitude_km, "altitude_km", 0)
        shell = SatelliteShell(
            group_id=group_id, constellation=self._group(constellation, Constellation),
            altitude_m=altitude_km * 1000, inclination_deg=inclination_deg, epoch=epoch,
            data={} if data is None else data,
        )
        if planes is None and satellites_per_plane is None:
            if (raan_start_deg != 0 or phase_start_deg != 0 or phase_factor != 0
                    or orbit_raan_deg is not None or id_prefix is not None or first_orbit != 1):
                raise ValueError("provide planes and satellites_per_plane for a generated shell")
            return self.add_group(shell)
        if isinstance(first_orbit, bool) or not isinstance(first_orbit, int) or first_orbit < 0:
            raise ValueError("first_orbit must be a nonnegative integer")
        prefix = group_id if id_prefix is None else id_prefix
        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("id_prefix must be a nonempty string")

        # Build the new group locally: a late conflict must not leave half a shell.
        groups = {group_id: shell}
        nodes = {}
        layout = circular_shell_layout(
            planes, satellites_per_plane, raan_start_deg=raan_start_deg,
            phase_start_deg=phase_start_deg, phase_factor=phase_factor,
            orbit_raan_deg=orbit_raan_deg,
        )
        for plane, (raan, phases) in enumerate(layout, start=first_orbit):
            orbit_id = f"{prefix}-orbit-{plane:03d}"
            if orbit_id in groups:
                raise ValueError(f"shell and generated orbit share an identity: {orbit_id}")
            orbit = SatelliteOrbit(group_id=orbit_id, shell=shell, raan_deg=raan)
            groups[orbit_id] = orbit
            for index, phase in enumerate(phases, start=1):
                node_id = f"{orbit_id}-satellite-{index:04d}"
                nodes[node_id] = Satellite(node_id=node_id, name=node_id, orbit=orbit,
                                           mean_anomaly_deg=phase)
        if self._groups.keys() & groups.keys() or self._nodes.keys() & nodes.keys():
            raise ValueError(f"shell identities already exist: {group_id}")
        self._groups.update(groups)
        self._nodes.update(nodes)
        return shell

    def create_orbit(self, group_id, *, shell, raan_deg, data=None):
        return self.add_group(SatelliteOrbit(
            group_id=group_id, shell=self._group(shell, SatelliteShell), raan_deg=raan_deg,
            data={} if data is None else data,
        ))

    def create_satellite(self, node_id, *, orbit=None, mean_anomaly_deg=None,
                         position=None, name=None, data=None):
        return self.add_node(Satellite(
            node_id=node_id, name=node_id if name is None else name,
            orbit=None if orbit is None else self._group(orbit, SatelliteOrbit),
            mean_anomaly_deg=mean_anomaly_deg, position_source=position,
            data={} if data is None else data,
        ))

    def create_ground_station_group(self, name, *, data=None):
        return self.add_group(GroundStationGroup(group_id=name, data={} if data is None else data))

    def create_ground_station(self, name, *, latitude_deg, longitude_deg, elevation_m=0,
                              node_id=None, group=None, data=None):
        return self.add_node(GroundStation(
            node_id=name if node_id is None else node_id, name=name,
            latitude_deg=latitude_deg, longitude_deg=longitude_deg, elevation_m=elevation_m,
            group=None if group is None else self._group(group, GroundStationGroup),
            data={} if data is None else data,
        ))

    def validate(self):
        """Check editable data; construction already checked fixed fields and references."""
        _validate_data(self.data, "Scene.data")
        for endpoint in self.endpoints:
            endpoint.validate()
        for group in self.groups:
            _validate_data(group.data, f"group {group.group_id!r}.data")
        for node in self.nodes:
            _validate_data(node.data, f"node {node.node_id!r}.data")
            if isinstance(node, Satellite) and node.position_source is not None:
                try:
                    node.position_source.validate()
                except ValueError as error:
                    raise ValueError(f"satellite {node.node_id!r}: {error}") from error

    def dump(self, path):
        """Save explicit identities and shared geometry once, as versioned JSON."""
        self.validate()
        groups = []
        for group in self.groups:
            item = {"group_id": group.group_id, "kind": group.kind,
                    "parent_id": group.parent_id, "data": group.data}
            if isinstance(group, SatelliteShell):
                item.update(altitude_m=group.altitude_m, inclination_deg=group.inclination_deg,
                            epoch=group.epoch.isoformat())
            elif isinstance(group, SatelliteOrbit):
                item["raan_deg"] = group.raan_deg
            groups.append(item)
        nodes = []
        for node in self.nodes:
            item = {"node_id": node.node_id, "name": node.name, "kind": node.kind, "data": node.data}
            if isinstance(node, Satellite):
                item.update(orbit_id=node.group_id, mean_anomaly_deg=node.mean_anomaly_deg,
                            position_source=None if node.position_source is None else asdict(node.position_source))
            else:
                item.update(group_id=node.group_id, latitude_deg=node.latitude_deg,
                            longitude_deg=node.longitude_deg, elevation_m=node.elevation_m)
            nodes.append(item)
        text = json.dumps({"format": _FORMAT, "version": _VERSION, "groups": groups,
                           "nodes": nodes, "data": self.data,
                           "endpoints": [asdict(endpoint) for endpoint in self.endpoints]},
                          indent=2, ensure_ascii=False,
                          allow_nan=False) + "\n"
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f".{path.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(text)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        """Restore the domain relationships; reject unknown/old draft schemas."""
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        location = "document"
        try:
            if record["format"] != _FORMAT or record["version"] != _VERSION:
                raise ValueError("unsupported Scene format or version; regenerate earlier draft .scene files")
            scene = cls(data=record["data"])
            for item in record.get("endpoints", []):
                location = f"endpoint {item.get('endpoint_id')!r}"
                scene.add_endpoint(AccessEndpoint(**item))
            # Fixed domain levels allow parent-first adoption even if records in
            # the file are unordered. References must resolve to the right type.
            buckets = {kind: [] for kind in _GROUP_TYPES}
            for item in record["groups"]:
                location = f"group {item['group_id']!r}"
                buckets[item["kind"]].append(item)
            for kind, items in buckets.items():
                for item in items:
                    location = f"group {item['group_id']!r}"
                    fields = dict(item)
                    del fields["kind"]
                    parent_id = fields.pop("parent_id")
                    if kind == "shell":
                        fields["constellation"] = scene._group(parent_id, Constellation)
                        fields["epoch"] = datetime.fromisoformat(fields["epoch"])
                    elif kind == "orbit":
                        fields["shell"] = scene._group(parent_id, SatelliteShell)
                    elif parent_id is not None:
                        raise ValueError(f"{kind} cannot have a parent group")
                    scene.add_group(_GROUP_TYPES[kind](**fields))
            # Preserve authored enumeration order without duplicating objects.
            scene._groups = {item["group_id"]: scene._groups[item["group_id"]] for item in record["groups"]}
            for item in record["nodes"]:
                location = f"node {item['node_id']!r}"
                fields = dict(item)
                kind = fields.pop("kind")
                if kind == "satellite":
                    orbit_id = fields.pop("orbit_id")
                    fields["orbit"] = None if orbit_id is None else scene._group(orbit_id, SatelliteOrbit)
                    source = fields["position_source"]
                    if source is not None:
                        fields["position_source"] = ScenePositionSource(**source)
                    node = Satellite(**fields)
                elif kind == "ground_station":
                    group_id = fields.pop("group_id")
                    fields["group"] = None if group_id is None else scene._group(group_id, GroundStationGroup)
                    node = GroundStation(**fields)
                else:
                    raise ValueError(f"unsupported Scene node kind: {kind}")
                scene.add_node(node)
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f"cannot load Scene {path}, {location}: {error}") from error
        return scene
