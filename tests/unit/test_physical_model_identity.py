"""Replaceable physical algorithms declare identity without exposing their internals."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

import pytest

from skyseed.configuration.simulator import (
    LatitudeLongitudeRectangleDefinition, ZoneDefinition, ZoneLayoutDefinition,
)
from skyseed.simulator import CircularOrbitPositionModel, EarthFixedPosition, Simulator
from skyseed.simulator.input import SimulatorStaticInput
from tests.unit.test_simulator_physical_world import (
    definition_for, ground_station_input, satellite_input,
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUT = SimulatorStaticInput(
    (satellite_input(65001, 'sat', 'shell', 'orbit', 0),),
    (ground_station_input(65001, 'gs'),),
)
ZONES = ZoneLayoutDefinition(zones=(ZoneDefinition(
    zone_id='zone', geometry=LatitudeLongitudeRectangleDefinition(-1, 1, -1, 1),
),), allow_overlap=False)


def make_world(position=None, membership=None, *, static_input=INPUT, zones=ZONES):
    return Simulator(definition_for(static_input)).create_physical_world(
        position_model=CircularOrbitPositionModel() if position is None else position,
        zone_membership_model=membership, zone_layout_definition=zones,
    )


class PositionCache:
    __slots__ = ('_cache', '_calls')


class DelegatingPosition(PositionCache):
    __slots__ = ('_delegate',)

    def __init__(self, cache=None):
        self._cache = {} if cache is None else cache
        self._calls = 0
        self._delegate = self._calculate

    def compute_positions(self, world, timestamp):
        return self._delegate(world, timestamp)

    def _calculate(self, world, timestamp):
        self._calls += 1
        self._cache[timestamp] = {node.node_id: EarthFixedPosition(7_000_000, 0, 0)
                                 for node in INPUT.list_nodes()}
        return self._cache[timestamp]

    def describe_configuration(self):
        raise AssertionError('provenance is not an identity prerequisite')


class DelegatingMembership:
    def __init__(self, cache=None):
        self._cache = {} if cache is None else cache
        self._calls = 0
        self._delegate = self._find

    def find_zones(self, zones, latitude, longitude):
        return self._delegate(zones, latitude, longitude)

    def _find(self, zones, latitude, longitude):
        self._calls += 1
        matches = tuple(z for z in zones if z.contains(latitude, longitude))
        self._cache[(latitude, longitude)] = matches
        return matches

    def resolve_zone(self, matches, latitude, longitude):
        self._calls += 1
        return min(matches, key=lambda z: z.zone_id)

    def describe_configuration(self):
        raise AssertionError('provenance is not an identity prerequisite')


def test_private_position_delegate_and_cache_do_not_participate_in_identity():
    cold, warm = DelegatingPosition(), DelegatingPosition({'unrelated': object()})
    first, second = make_world(cold), make_world(warm)
    fingerprint = first.semantic_fingerprint()
    assert cold._calls == warm._calls == 0
    assert second.semantic_fingerprint() == fingerprint
    assert dict(first.frame_at(EPOCH).positions) == dict(second.frame_at(EPOCH).positions)
    assert cold._calls == warm._calls == 1
    assert len(cold._cache) == 1 and len(warm._cache) == 2
    assert first.semantic_fingerprint() is fingerprint
    assert make_world(DelegatingPosition(cold._cache)).semantic_fingerprint() == fingerprint


def test_private_membership_delegate_and_cache_do_not_participate_in_identity():
    cold, warm = DelegatingMembership(), DelegatingMembership({'unrelated': object()})
    first, second = make_world(membership=cold), make_world(membership=warm)
    fingerprint = first.semantic_fingerprint()
    assert cold._calls == warm._calls == 0
    assert second.semantic_fingerprint() == fingerprint
    assert first.find_zones(0, 0) == second.find_zones(0, 0)
    assert first.resolve_zone(0, 0) is first.get_zone('zone')
    assert cold._calls == 3 and warm._calls == 1
    assert first.semantic_fingerprint() is fingerprint
    assert make_world(membership=DelegatingMembership(cold._cache)).semantic_fingerprint() == fingerprint


@pytest.mark.parametrize('role', ['position', 'membership'])
def test_explicit_semantic_configuration_distinguishes_same_type_instances(role):
    base = DelegatingPosition if role == 'position' else DelegatingMembership

    class Declared(base):
        def __init__(self, configuration):
            super().__init__()
            self.configuration = configuration
            self.identity_calls = 0

        def semantic_fingerprint_material(self):
            self.identity_calls += 1
            return {'configuration': self.configuration}

    first_model, equivalent, changed = Declared(1), Declared(1), Declared(2)
    first = make_world(**{role: first_model})
    fingerprint = first.semantic_fingerprint()
    assert make_world(**{role: equivalent}).semantic_fingerprint() == fingerprint
    assert make_world(**{role: changed}).semantic_fingerprint() != fingerprint
    first.frame_at(EPOCH)
    first.resolve_zone(0, 0)
    assert first.semantic_fingerprint() is fingerprint
    assert first_model.identity_calls == equivalent.identity_calls == changed.identity_calls == 1


class OpaqueMaterial:
    def __getattribute__(self, name):
        if name in {'__dict__', '__slots__', 'semantic_fingerprint_material', '_private'}:
            raise AssertionError('opaque identity fields were inspected')
        return super().__getattribute__(name)


@pytest.mark.parametrize('role', ['position', 'membership'])
@pytest.mark.parametrize('kind', ['opaque', 'callable', 'cycle', 'nonfinite', 'hook-error', 'noncallable'])
def test_invalid_explicit_material_is_an_input_error(role, kind):
    base = DelegatingPosition if role == 'position' else DelegatingMembership
    material = {'opaque': OpaqueMaterial(), 'callable': lambda: None,
                'cycle': [], 'nonfinite': float('nan')}.get(kind)
    if kind == 'cycle':
        material.append(material)

    class Declared(base):
        def semantic_fingerprint_material(self):
            if kind == 'hook-error':
                raise RuntimeError('explicit declaration failed')
            return {'nested': material}

    if kind == 'noncallable':
        Declared.semantic_fingerprint_material = 7
    error, message = {
        'opaque': (TypeError, 'unsupported.*OpaqueMaterial'),
        'callable': (TypeError, 'callables'),
        'cycle': (ValueError, 'acyclic'),
        'nonfinite': (ValueError, 'finite'),
        'hook-error': (RuntimeError, 'explicit declaration failed'),
        'noncallable': (TypeError, 'must be callable'),
    }[kind]
    with pytest.raises(error, match=message):
        make_world(**{role: Declared()})


def test_explicit_supported_data_values_remain_canonical():
    class Choice(Enum):
        VALUE = 'value'

    @dataclass(frozen=True)
    class Setting:
        value: int

    class Declared(DelegatingPosition):
        def __init__(self, reverse):
            super().__init__()
            self.reverse = reverse

        def semantic_fingerprint_material(self):
            pairs = [('time', EPOCH), ('duration', timedelta(seconds=3)),
                     ('node', INPUT.list_satellites()[0].node_id), ('enum', Choice.VALUE),
                     ('setting', Setting(5)), ('set', {2, 1}),
                     ('sequence', (None, True, 1, 0.5, 'text'))]
            return dict(reversed(pairs) if self.reverse else pairs)

    assert make_world(Declared(False)).semantic_fingerprint() == make_world(Declared(True)).semantic_fingerprint()


@pytest.mark.parametrize('change', ['node', 'orbit', 'station', 'zone', 'geometry', 'overlap'])
def test_real_static_world_changes_still_change_identity(change):
    static_input, zones = INPUT, ZONES
    satellite, = INPUT.list_satellites()
    station, = INPUT.list_ground_stations()
    if change == 'node':
        static_input = SimulatorStaticInput((replace(satellite,
            satellite_id='other', node_id=replace(satellite.node_id, entity_id='other')),), (station,))
    elif change == 'orbit':
        static_input = SimulatorStaticInput((replace(satellite, altitude_km=600),), (station,))
    elif change == 'station':
        static_input = SimulatorStaticInput((satellite,), (replace(station, elevation_m=20),))
    elif change == 'zone':
        zones = replace(ZONES, zones=(replace(ZONES.zones[0], zone_id='other'),))
    elif change == 'geometry':
        zones = replace(ZONES, zones=(replace(ZONES.zones[0], geometry=replace(
            ZONES.zones[0].geometry, north_latitude_deg=2)),))
    else:
        zones = replace(ZONES, allow_overlap=True)
    assert make_world(static_input=static_input, zones=zones).semantic_fingerprint() != make_world().semantic_fingerprint()


def test_default_models_preserve_the_reviewed_fingerprint_after_cache_population():
    # The existing identity contract includes fully qualified Model type names.
    # Same fixture after skyseed_v2 -> skyseed; cache population remains irrelevant.
    expected = '4ab2db63f6a9b6350cfb66ab3eebc59d62daf4f76f716ec8ebe8b5a7953bf456'
    world = make_world()
    assert world.semantic_fingerprint() == expected
    world.frame_at(EPOCH)
    assert world.semantic_fingerprint() == expected
