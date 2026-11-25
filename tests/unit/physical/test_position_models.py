"""Independent geometric, derivative and published SGP4 expectations."""
from datetime import datetime, timezone, timedelta
from math import cos, sin, pi, sqrt
import numpy as np
import pytest

from skyseed import Scene
from skyseed.scene import ScenePositionSource
from skyseed.pc.physical import PhysicalLayer, CircularOrbitPositionModel, SGP4PositionModel, tle_position_source

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Vallado's satellite 00005 verification case, distributed with python-sgp4.
LINE1 = '1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753'
LINE2 = '2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667'


def circular_scene():
    scene = Scene()
    scene.create_constellation('c', planes=1, satellites_per_plane=2, altitude_km=550,
                               inclination_deg=53, epoch=EPOCH)
    scene.create_ground_station('equator', latitude_deg=0, longitude_deg=0, elevation_m=20)
    scene.create_ground_station('pole', latitude_deg=90, longitude_deg=0)
    return scene


def test_fixed_ground_and_circular_geometry():
    physical = PhysicalLayer(circular_scene())
    snapshot = physical.snapshot_at(EPOCH)
    first, opposite = (snapshot[f'c/shell-0/plane-0/sat-{i}'] for i in (0, 1))
    assert np.linalg.norm(first.position_m) == pytest.approx(6928137, abs=1e-6)
    np.testing.assert_allclose(first.position_m, -np.array(opposite.position_m), atol=1e-6)
    assert snapshot['equator'].position_m == pytest.approx((6378157, 0, 0), abs=1e-8)
    assert snapshot['pole'].position_m == pytest.approx((0, 0, 6356752.314245), abs=1e-6)
    assert snapshot['equator'].velocity_m_s == (0, 0, 0)
    later = physical.snapshot_at(EPOCH + timedelta(seconds=10), ['equator'])
    assert later['equator'] is snapshot['equator']


def test_circular_velocity_is_ecef_derivative_not_rotated_inertial_speed():
    physical = PhysicalLayer(circular_scene())
    identity = 'c/shell-0/plane-0/sat-0'
    before = physical.snapshot_at(EPOCH - timedelta(seconds=.1), [identity])[identity]
    state = physical.snapshot_at(EPOCH, [identity])[identity]
    after = physical.snapshot_at(EPOCH + timedelta(seconds=.1), [identity])[identity]
    numerical = (np.array(after.position_m) - before.position_m) / .2
    np.testing.assert_allclose(state.velocity_m_s, numerical, atol=.003, rtol=0)
    inertial_speed = sqrt(3.986004418e14 / 6928137)
    assert abs(np.linalg.norm(state.velocity_m_s) - inertial_speed) > 100


def test_position_subset_empty_and_timezone():
    scene = circular_scene()
    model = CircularOrbitPositionModel()
    assert model.compute_positions(scene, (), EPOCH) == {}
    identity = scene.get_satellites()[1].node_id
    assert tuple(model.compute_positions(scene, [identity], EPOCH)) == (identity,)
    physical = PhysicalLayer(scene)
    local_time = EPOCH.astimezone(timezone(timedelta(hours=8)))
    assert physical.snapshot_at(local_time, [identity])[identity] == physical.snapshot_at(EPOCH, [identity])[identity]
    with pytest.raises(ValueError, match='timezone-aware'):
        physical.snapshot_at(EPOCH.replace(tzinfo=None))
    with pytest.raises(KeyError):
        physical.snapshot_at(EPOCH, ['missing'])
    with pytest.raises(ValueError, match='not a satellite'):
        model.compute_positions(scene, ['equator'], EPOCH)


def test_circular_motion_counts_the_2016_leap_second():
    scene = Scene()
    for identity, epoch in [('before','2016-12-31T23:59:59+00:00'), ('after','2017-01-01T00:00:00+00:00')]:
        scene.create_satellite(identity, position=ScenePositionSource('circular-orbit/v1', {
            'altitude_m':550000,'inclination_deg':53,'raan_deg':0,'mean_anomaly_deg':0,'epoch':epoch}))
    frame = PhysicalLayer(scene).snapshot_at(datetime(2017,1,1,tzinfo=timezone.utc))
    distance = np.linalg.norm(np.array(frame['before'].position_m)-frame['after'].position_m)
    radius = 6928137
    motion = sqrt(3.986004418e14 / radius**3)
    # Two physical seconds separate these UTC labels, not datetime's one.
    assert distance == pytest.approx(2*radius*sin(motion), abs=1e-5)


def test_editable_source_refreshes_prepared_elements():
    scene = Scene()
    node = scene.create_satellite('s', position=ScenePositionSource('circular-orbit/v1', {
        'altitude_m':550000, 'inclination_deg':0, 'raan_deg':0, 'mean_anomaly_deg':0,
        'epoch':EPOCH.isoformat()}))
    physical = PhysicalLayer(scene)
    old = physical.snapshot_at(EPOCH)['s']
    node.position_source.data['mean_anomaly_deg'] = 180
    new = physical.snapshot_at(EPOCH)['s']
    np.testing.assert_allclose(new.position_m, -np.array(old.position_m), atol=1e-6)
    node.position_source.data['altitude_m'] = -1
    with pytest.raises(ValueError, match='altitude_m'):
        physical.snapshot_at(EPOCH)


def test_sgp4_published_vector_and_ecef_derivative():
    scene = Scene()
    scene.create_satellite('vanguard', position=tle_position_source(LINE1, LINE2, source='Vallado verification'))
    physical = PhysicalLayer(scene)
    physical.set_position_model(SGP4PositionModel)
    epoch = datetime(2000, 6, 27, 18, 50, 19, 733568, tzinfo=timezone.utc)
    state = physical.snapshot_at(epoch)['vanguard']
    # Published TEME km vector at the element epoch. Independently rotate using
    # the standard approximate GMST expression; omit polar motion/UT1 (<100 m).
    reference = np.array([7022.46529266, -1400.08296755, .03995155]) * 1000
    jd = 2451723.28495062
    centuries = (jd - 2451545) / 36525
    theta = (280.46061837 + 360.98564736629 * (jd - 2451545)
             + .000387933 * centuries**2 - centuries**3 / 38710000) * pi / 180
    expected = (cos(theta)*reference[0] + sin(theta)*reference[1],
                -sin(theta)*reference[0] + cos(theta)*reference[1], reference[2])
    np.testing.assert_allclose(state.position_m, expected, atol=100, rtol=0)
    assert np.linalg.norm(state.position_m) == pytest.approx(np.linalg.norm(reference), abs=.01)
    reference_velocity = np.array([1.893841015, 6.405893759, 4.534807250]) * 1000
    rotated = np.array([cos(theta)*reference_velocity[0]+sin(theta)*reference_velocity[1],
                        -sin(theta)*reference_velocity[0]+cos(theta)*reference_velocity[1], reference_velocity[2]])
    expected_velocity = rotated - np.cross((0,0,7.292115e-5), expected)
    np.testing.assert_allclose(state.velocity_m_s, expected_velocity, atol=.1, rtol=0)
    before = physical.snapshot_at(epoch-timedelta(seconds=.1))['vanguard']
    after = physical.snapshot_at(epoch+timedelta(seconds=.1))['vanguard']
    # This SGP4 verification orbit's raw TEME velocity differs by ~1 m/s
    # from differencing its positions. Use its published velocity above as the
    # tight reference; this coarser derivative check still catches frame errors.
    np.testing.assert_allclose(state.velocity_m_s,
                              (np.array(after.position_m)-before.position_m)/.2, atol=2, rtol=0)
    assert physical.position_model.compute_positions(scene, (), epoch) == {}


def test_tle_bad_input_and_wrong_model_are_useful_errors():
    with pytest.raises(ValueError, match='checksum'):
        tle_position_source(LINE1[:-1]+'0', LINE2)
    with pytest.raises(ValueError, match='69-character'):
        tle_position_source('missing', LINE2)
    scene = Scene()
    scene.create_satellite('vanguard', position=tle_position_source(LINE1, LINE2))
    with pytest.raises(ValueError, match='vanguard.*circular-orbit'):
        PhysicalLayer(scene).snapshot_at(EPOCH)
