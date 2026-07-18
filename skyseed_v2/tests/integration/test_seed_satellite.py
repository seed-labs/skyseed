import pytest

from seedemu.core import Emulator, Node
from seedemu.core.enums import NodeRole

from skyseed_v2.simulator_input_generation import create_simulator_static_input
from skyseed_v2.node_id import NodeId
from skyseed_v2.entities import OrbitParameters, Satellite, SatelliteRole
from skyseed_v2.seedemu_integration import (
    SATELLITE_REGISTRY_TYPE,
    SatelliteBase,
    SkyseedAutonomousSystem,
)
from skyseed_v2.simulator_input import SatelliteSimulationInput


def build_rendered_emulator():
    emulator = Emulator()
    base = SatelliteBase()
    satellite_as = SkyseedAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)
    shell = satellite_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(550.0, 53.0, 15.0),
    )
    orbit.create_satellites(2, satellite_id_prefix="satellite")
    emulator.addLayer(base)
    emulator.render()
    return emulator, satellite_as, shell, orbit


def test_orbit_satellites_are_the_registered_seed_nodes() -> None:
    emulator, satellite_as, _, orbit = build_rendered_emulator()
    registry = emulator.getRegistry()

    registered_satellites = registry.getByType(
        "65000", SATELLITE_REGISTRY_TYPE
    )

    assert len(registered_satellites) == 2
    assert all(isinstance(satellite, Satellite) for satellite in registered_satellites)
    assert all(isinstance(satellite, Node) for satellite in registered_satellites)
    assert all(
        registered is created
        for registered, created in zip(
            registered_satellites, orbit.satellites, strict=True
        )
    )
    assert all(
        satellite.getRole() is SatelliteRole.SATELLITE
        for satellite in registered_satellites
    )
    assert all(
        satellite.getRole() is not NodeRole.Host
        for satellite in registered_satellites
    )
    assert registry.getByType("65000", "hnode") == []
    assert not hasattr(satellite_as, "_satellite_nodes")
    assert not hasattr(satellite_as, "get_satellite_node")


def test_only_satellites_not_shell_or_orbit_enter_registry() -> None:
    emulator, _, shell, orbit = build_rendered_emulator()
    registered_objects = tuple(emulator.getRegistry().getAll().values())

    assert shell not in registered_objects
    assert orbit not in registered_objects
    assert all(satellite in registered_objects for satellite in orbit.satellites)


def test_base_render_and_options_apply_to_the_orbit_satellite_instances() -> None:
    emulator, _, _, orbit = build_rendered_emulator()
    nodes = emulator.getRegistry().getByType("65000", SATELLITE_REGISTRY_TYPE)

    for registered, satellite in zip(nodes, orbit.satellites, strict=True):
        assert registered is satellite
        assert satellite.getInterfaces() == []
        assert satellite.getOption("sysctl_netipv4_ip_forward") is not None
        assert satellite.getOption("sysctl_netipv4_conf_rp_filter") is not None
        files = {file.get()[0]: file.get()[1] for file in satellite.getFiles()}
        assert files["/ifinfo.txt"] == ""
        assert "/interface_setup" in files


def test_simulation_input_is_a_static_copy_of_registered_satellite() -> None:
    emulator, satellite_as, _, _ = build_rendered_emulator()

    for simulation_input in create_simulator_static_input(
        (satellite_as,)
    ).list_satellites():
        satellite = satellite_as.get_satellite(simulation_input.satellite_id)
        registered = emulator.getRegistry().get(
            "65000", SATELLITE_REGISTRY_TYPE, simulation_input.satellite_id
        )
        assert registered is satellite
        assert simulation_input.satellite_id == satellite.satellite_id
        assert simulation_input.node_id == NodeId(
            asn=satellite.getAsn(),
            entity_kind="satellite",
            entity_id=satellite.satellite_id,
        )
        assert not isinstance(simulation_input.node_id, Node)
        assert "satnode" not in simulation_input.node_id.value


def test_as_rejects_duplicate_shell_and_satellite_ids_across_shells() -> None:
    satellite_as = SkyseedAutonomousSystem(65000)
    first_shell = satellite_as.create_shell("shell-1")
    with pytest.raises(ValueError, match="shell_id"):
        satellite_as.create_shell("shell-1")

    second_shell = satellite_as.create_shell("shell-2")
    first_orbit = first_shell.create_orbit(
        "orbit-1", parameters=OrbitParameters(550.0, 53.0, 15.0)
    )
    second_orbit = second_shell.create_orbit(
        "orbit-1", parameters=OrbitParameters(550.0, 53.0, 25.0)
    )
    first_orbit.create_satellite(
        "satellite-0", orbit_index=0, mean_anomaly_deg=0.0
    )

    with pytest.raises(ValueError, match="satellite_id"):
        second_orbit.create_satellite(
            "satellite-0", orbit_index=0, mean_anomaly_deg=180.0
        )
