from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import NodeState, PhysicalPositionSnapshot
from skyseed.pc.network import HighestElevationServiceAssignmentModel, ServiceAssignmentModel
import skyseed.pc.network.service_assignment_model as service_module


def test_highest_elevation_and_unserved_geographic_endpoints(epoch):
    scene = Scene()
    for node in ("overhead", "slanted", "behind"):
        scene.create_satellite(node, position=ScenePositionSource("test/static", {}))
    scene.create_zone("near", latitude_deg=0, longitude_deg=0)
    scene.create_zone("unseen", latitude_deg=90, longitude_deg=0)
    scene.create_endpoint("ut", kind="terminal/v1", data={})
    snapshot = PhysicalPositionSnapshot(scene, epoch, {
        "overhead": NodeState((6928137, 0, 0)),
        "slanted": NodeState((6928137, 500000, 0)),
        "behind": NodeState((-6928137, 0, 0)),
    })
    model = HighestElevationServiceAssignmentModel()
    assert model.assign_service(scene, snapshot) == {"near": "overhead", "unseen": None}


def test_high_orbit_does_not_expand_low_orbit_search(epoch, monkeypatch):
    scene = Scene()
    scene.create_zone("near", latitude_deg=0, longitude_deg=0)
    positions = {"near": (6928137, 0, 0), "geo": (42164137, 1000000, 0)}
    for index in range(128):
        positions[f"far-{index}"] = (-6928137, index * 100, 0)
    for node in positions:
        scene.create_satellite(node, position=ScenePositionSource("test/static", {}))
    snapshot = PhysicalPositionSnapshot(scene, epoch, {node: NodeState(value) for node, value in positions.items()})
    checked = []
    original = service_module.earth_visible
    def observe(point, candidates):
        checked.append(len(candidates))
        return original(point, candidates)
    monkeypatch.setattr(service_module, "earth_visible", observe)
    result = HighestElevationServiceAssignmentModel().assign_service(scene, snapshot)
    assert result == {"near": "near"}
    assert sum(checked) == 2


def test_custom_assignment_can_follow_actual_access_and_query_ground_position(epoch, network_factory):
    from datetime import timedelta
    network = network_factory()
    class Attachments(ServiceAssignmentModel):
        def assign_service(self, scene, snapshot):
            assert snapshot["g"].position_m == (6378137.0, 0.0, 0.0)
            links = self.layer.link_state_at(snapshot.timestamp)
            candidates = [peer for link in links.values() for peer in link.candidate.endpoints
                          if "g" in link.candidate.endpoints and peer != "g"]
            return {"z": candidates[0] if candidates else None}
    network.set_service_assignment_model(Attachments)
    network.generate_plans(epoch, epoch + timedelta(seconds=1))
    assert network.service_assignments_at(epoch) == {"z": "a"}


def test_low_elevation_service_is_not_lost_by_ellipsoid_horizon_filter(epoch):
    from math import cos, radians, sin, sqrt
    from skyseed.pc.physical.earth_geometry import ground_ecef

    scene = Scene()
    scene.create_zone("z", latitude_deg=45, longitude_deg=0)
    scene.create_satellite("s", position=ScenePositionSource("test/static", {}))
    point = ground_ecef(45, 0, 0)
    up = (1 / sqrt(2), 0, 1 / sqrt(2))
    north = (-1 / sqrt(2), 0, 1 / sqrt(2))
    elevation = radians(0.01)
    # An outward ray above the local tangent plane cannot cross the ellipsoid.
    position = tuple(point[axis] + 4_000_000 * (
        cos(elevation) * north[axis] + sin(elevation) * up[axis]
    ) for axis in range(3))
    snapshot = PhysicalPositionSnapshot(scene, epoch, {"s": NodeState(position)})
    model = HighestElevationServiceAssignmentModel(min_elevation_deg=0)
    assert model.assign_service(scene, snapshot) == {"z": "s"}
