"""Own Scene queries, fixed ground states, and reusable position/candidate models."""
from types import MappingProxyType

from .physical_results import NodeState, PhysicalPositionSnapshot, LinkCandidate, utc_time
from .earth_geometry import ground_ecef
from .position_model import CircularOrbitPositionModel
from .link_candidate_model import GeometricCandidateModel


class PhysicalLayer:
    def __init__(self, scene):
        self.scene = scene
        self._ground_states = {}
        self.set_position_model(CircularOrbitPositionModel)
        self.set_candidate_model(GeometricCandidateModel)

    def _model(self, model_class, options, method):
        if not isinstance(model_class, type):
            raise TypeError("register a model class, not an instance or None")
        if options is None:
            options = {}
        model = model_class(**options)
        if not callable(getattr(model, method, None)):
            raise TypeError(f"{model_class.__name__} must implement {method}")
        # One context reference, no lifecycle wrapper or forwarding manager.
        model.layer = self
        return model

    def set_position_model(self, model_class, *, model_options=None):
        self.position_model = self._model(model_class, model_options, "compute_positions")

    def set_candidate_model(self, model_class, *, model_options=None):
        self.candidate_model = self._model(model_class, model_options, "compute_candidates")

    def _node_ids(self, node_ids):
        if node_ids is None:
            return tuple(self.scene.get_node_ids())
        if isinstance(node_ids, str):
            raise TypeError("node_ids must be a collection of IDs, not one string")
        return tuple(dict.fromkeys(node_ids))

    def snapshot_at(self, timestamp, node_ids=None):
        timestamp = utc_time(timestamp)
        identities = self._node_ids(node_ids)
        satellites = []
        ground = {}
        for node_id in identities:
            node = self.scene.get_node(node_id)
            if node.kind == "satellite":
                satellites.append(node_id)
            else:
                cached = self._ground_states.get(node_id)
                if cached is None or cached[0] is not node:
                    position = ground_ecef(
                        node.latitude_deg, node.longitude_deg, node.elevation_m
                    )
                    state = NodeState(position, (0, 0, 0))
                    self._ground_states[node_id] = (node, state)
                ground[node_id] = self._ground_states[node_id][1]
        states = {}
        if satellites:
            states = dict(self.position_model.compute_positions(
                self.scene, tuple(satellites), timestamp
            ))
        expected_ids = set(satellites)
        if states.keys() != expected_ids:
            missing = expected_ids - states.keys()
            unexpected = states.keys() - expected_ids
            raise ValueError(
                f"position model returned wrong satellite IDs: "
                f"missing={missing}, unexpected={unexpected}"
            )
        for node_id, state in states.items():
            if not isinstance(state, NodeState):
                raise TypeError(f"position model result for {node_id!r} must be NodeState")
        states.update(ground)
        ordered_states = {node_id: states[node_id] for node_id in identities}
        return PhysicalPositionSnapshot(self.scene, timestamp, ordered_states)

    def candidates(self, snapshot, node_ids=None):
        """Return per-node reports, preserving custom fields and orientation.

        The snapshot is the peer universe. No missing nodes are computed behind
        the caller's back. Keeping reports separate avoids silently merging
        different directional/custom observations of the same pair.
        """
        if snapshot.scene is not self.scene:
            raise ValueError("snapshot belongs to a different Scene")
        if node_ids is None:
            identities = tuple(snapshot.states)
        else:
            identities = self._node_ids(node_ids)
        for node_id in identities:
            if node_id not in snapshot.states:
                raise ValueError(f"candidate query node {node_id!r} is missing from snapshot")
        result = {}
        for node_id in identities:
            candidates = tuple(self.candidate_model.compute_candidates(
                self.scene, snapshot, node_id
            ))
            peers = set()
            for candidate in candidates:
                if not isinstance(candidate, LinkCandidate):
                    raise TypeError(f"candidate model result for {node_id!r} must be LinkCandidate")
                if candidate.node_id != node_id or candidate.peer_id not in snapshot.states:
                    raise ValueError(
                        f"candidate for {node_id!r} has wrong owner or peer outside snapshot"
                    )
                if candidate.peer_id in peers:
                    raise ValueError(f"duplicate candidate peer {candidate.peer_id!r} for {node_id!r}")
                peers.add(candidate.peer_id)
            result[node_id] = candidates
        return MappingProxyType(result)

    def candidates_at(self, timestamp, node_ids=None, *, snapshot_ids=None):
        """Prepare one snapshot and query chosen centres (all snapshot nodes by default)."""
        snapshot = self.snapshot_at(timestamp, snapshot_ids)
        return self.candidates(snapshot, node_ids)
