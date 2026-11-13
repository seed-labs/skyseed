from datetime import datetime, timezone
import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, NodeState, LinkCandidate
from skyseed.pc.link import ConnectionCandidate, LinkProperties, LinkConfiguration, LinkLayer

TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def epoch():
    return TIME


@pytest.fixture
def configuration():
    def make(first='a', second='b', *, delay=0.01, reverse_delay=None):
        candidate = ConnectionCandidate((first, second), {
            first: LinkCandidate(first, second, propagation_delay_s=delay),
            second: LinkCandidate(second, first, propagation_delay_s=delay),
        })
        forward = LinkProperties(candidate.id, 1_000_000, delay)
        reverse = None
        if reverse_delay is not None:
            reverse = LinkProperties(candidate.id, 1_000_000, reverse_delay)
        return LinkConfiguration(candidate, forward, reverse)
    return make


@pytest.fixture
def scheduled_layer():
    """Real Physical layer with controlled per-direction reports, no mock Link."""
    def make(schedule):
        scene = Scene()
        for node_id in ('a', 'b', 'c'):
            scene.create_satellite(node_id, position=ScenePositionSource('test/linear', {}))

        class Positions:
            def compute_positions(self, scene, satellite_ids, timestamp):
                return {node: NodeState((6_928_137, 1000 * ord(node), 0)) for node in satellite_ids}

        class Candidates:
            def __init__(self):
                self.calls = []

            def compute_candidates(self, scene, snapshot, node_id):
                seconds = (snapshot.timestamp - TIME).total_seconds()
                self.calls.append((seconds, node_id))
                return tuple(LinkCandidate(node_id, peer, propagation_delay_s=delay,
                                           data={'source': node_id})
                             for (source, peer), delay in schedule(seconds).items() if source == node_id)

        physical = PhysicalLayer(scene)
        physical.set_position_model(Positions)
        physical.set_candidate_model(Candidates)
        link = LinkLayer(physical)
        link.set_selection_hook(None)
        return link
    return make
