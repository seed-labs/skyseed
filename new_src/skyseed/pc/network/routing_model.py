"""Routing contract and GZ's destination-rooted path/label computation."""
from collections import deque
from dataclasses import dataclass
from math import cos, radians, sin
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping, Protocol, Sequence

from skyseed.scene import Scene
from ..link import LinkPlan
from .forwarding_rules import ForwardingRule, LogicalExit, RouteDestination

if TYPE_CHECKING:
    from .network_layer import NetworkLayer


class RoutingModel(Protocol):
    layer: "NetworkLayer"

    def compute_routes(self, scene: Scene, timestamp, link_plans: Sequence[LinkPlan]
                       ) -> Mapping[str, Sequence[ForwardingRule]]: ...


@dataclass(frozen=True, slots=True)
class GZRouteEvaluation:
    timestamp: object
    assignments: Mapping
    associations: Mapping
    paths: Mapping  # (source node, RouteDestination) -> node tuple or None


class GroundStationZoneRoutingModel:
    """Minimum hops, GS endpoints only, locally assigned downstream labels.

    label_forwarding=False emits destination/exit rules for IP-oriented
    compilation. The algorithm and logical destinations are otherwise shared.
    """
    def __init__(self, *, ground_stations_per_zone=1, label_forwarding=True, observer=None):
        if type(ground_stations_per_zone) is not int or ground_stations_per_zone < 1:
            raise ValueError("ground_stations_per_zone must be a positive integer")
        self.ground_stations_per_zone = ground_stations_per_zone
        self.label_forwarding = label_forwarding
        self.observer = observer
        self._scene = None
        self._labels = {}
        self._next_label = {}
        self._trees = {}
        self._component_signatures = {}

    def label_bindings(self):
        """Node-local incoming bindings; retained numbers are never reassigned."""
        return MappingProxyType(dict(self._labels))

    def _label(self, node, destination):
        key = (node, destination)
        if key not in self._labels:
            label = self._next_label.get(node, 16)
            if label >= 2**20:
                raise ValueError(f"incoming label space exhausted at {node!r}")
            self._labels[key] = label
            self._next_label[node] = label + 1
        return self._labels[key]

    def _prepare_scene(self, scene):
        if self._scene is scene:
            return
        self._satellites = {node.node_id for node in scene.get_satellites()}
        self._grounds = {node.node_id: node for node in scene.get_ground_stations()}
        associations = {}
        for zone in scene.get_endpoints("geographic-zone/v1"):
            latitude, longitude = radians(zone.data["latitude_deg"]), radians(zone.data["longitude_deg"])
            def distance_key(node):
                other_latitude = radians(node.latitude_deg)
                other_longitude = radians(node.longitude_deg)
                cosine = sin(latitude) * sin(other_latitude)
                cosine += cos(latitude) * cos(other_latitude) * cos(longitude - other_longitude)
                return (-cosine, node.node_id)
            closest = sorted(self._grounds.values(), key=distance_key)[:self.ground_stations_per_zone]
            associations[zone.endpoint_id] = tuple(node.node_id for node in closest)
        self._associations = MappingProxyType(associations)
        self._scene = scene

    def _graph(self, links):
        forward = {node: [] for node in self._satellites | self._grounds.keys()}
        reverse = {node: [] for node in forward}
        for link in links.values():
            for source, report in link.candidate.reports.items():
                peer = report.peer_id
                if source not in self._satellites and peer not in self._satellites:
                    continue
                forward[source].append(peer)
                reverse[peer].append(source)
        forward = {node: tuple(sorted(peers)) for node, peers in forward.items()}
        reverse = {node: tuple(sorted(peers)) for node, peers in reverse.items()}
        # Reuse destination trees when their satellite component and GS access
        # edges are unchanged. Property-only updates never invalidate hop paths.
        components, signatures = {}, {}
        for node in sorted(self._satellites):
            if node in components:
                continue
            pending, members = [node], {node}
            while pending:
                current = pending.pop()
                for peer in forward[current] + reverse[current]:
                    if peer in self._satellites and peer not in members:
                        members.add(peer)
                        pending.append(peer)
            signature = tuple((member, forward[member], reverse[member]) for member in sorted(members))
            old = self._component_signatures.get(node)
            token = old[1] if old is not None and old[0] == signature else object()
            signatures[node] = (signature, token)
            for member in members:
                components[member] = token
        self._component_signatures = signatures
        return forward, reverse, components

    def _tree(self, target, forward, reverse, components):
        roots = (target,) if target in self._satellites else reverse[target]
        tokens = frozenset(components[root] for root in roots if root in components)
        dependency = (tokens, reverse[target])
        old = self._trees.get(target)
        if old is not None and old[0] == dependency:
            return old[1]
        distances = {target: 0}
        queue = deque([target])
        while queue:
            current = queue.popleft()
            for predecessor in reverse[current]:
                if predecessor in distances:
                    continue
                distances[predecessor] = distances[current] + 1
                if predecessor in self._satellites:
                    queue.append(predecessor)
        next_hops = {}
        for node, distance in distances.items():
            if node == target:
                continue
            next_hops[node] = next(peer for peer in forward[node]
                                   if (peer in self._satellites or peer == target)
                                   and distances.get(peer) == distance - 1)
        tree = (distances, next_hops)
        self._trees[target] = (dependency, tree)
        return tree

    def compute_routes(self, scene, timestamp, link_plans):
        self._prepare_scene(scene)
        assignments = self.layer.service_assignments_at(timestamp)
        links = self.layer.link_state_at(timestamp, link_plans)
        forward, reverse, components = self._graph(links)
        demands = {}
        paths = {}
        for endpoint, grounds in self._associations.items():
            serving = assignments.get(endpoint)
            destination = RouteDestination("endpoint", endpoint)
            if serving is None:
                for ground in grounds:
                    paths[(ground, destination)] = None
                continue
            demands[destination] = (serving, set(grounds))
            for ground in grounds:
                ground_destination = RouteDestination("node", ground)
                demands.setdefault(ground_destination, (ground, set()))[1].add(serving)
        tables = {}
        used_targets = set()
        for destination, (target, sources) in demands.items():
            distances, next_hops = self._tree(target, forward, reverse, components)
            used_targets.add(target)
            required = set()
            receivers = set()
            for source in sorted(sources):
                if source not in distances:
                    paths[(source, destination)] = None
                    continue
                path = [source]
                while path[-1] != target:
                    path.append(next_hops[path[-1]])
                paths[(source, destination)] = tuple(path)
                required.update(path)
                receivers.update(path[1:])
            # Egress first: its local binding becomes its predecessors' outgoing
            # label. Continue upstream in increasing distance, sharing branches.
            for node in sorted(required, key=lambda node: (distances[node], node)):
                table = tables.setdefault(node, {})
                exit = None if node == target else LogicalExit((node, next_hops[node]), node)
                if self.label_forwarding:
                    downstream = None if exit is None else self._labels[(exit.peer, destination)]
                    if node in receivers:
                        incoming = self._label(node, destination)
                        if exit is None:
                            rule = ForwardingRule(destination, "pop_deliver", incoming_label=incoming)
                        else:
                            rule = ForwardingRule(destination, "swap", exit, incoming, downstream)
                        table[rule.key] = rule
                    if node in sources:
                        if exit is None:
                            ingress = ForwardingRule(destination, "deliver")
                        else:
                            ingress = ForwardingRule(destination, "push", exit,
                                                     outgoing_label=downstream)
                        table[ingress.key] = ingress
                else:
                    rule = ForwardingRule(destination, "deliver" if exit is None else "forward", exit)
                    table[rule.key] = rule
        self._trees = {target: tree for target, tree in self._trees.items() if target in used_targets}
        if self.observer is not None:
            self.observer(GZRouteEvaluation(timestamp, assignments, self._associations,
                                            MappingProxyType(paths)))
        return {node: tuple(table.values()) for node, table in tables.items()}
