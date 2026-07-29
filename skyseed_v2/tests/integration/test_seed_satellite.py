from datetime import datetime, timezone

import pytest

from seedemu.core import Emulator, ExtensionNode
from seedemu.core.enums import NodeRole
from seedemu.layers import Base

from skyseed_v2.emulator.entities import OrbitParameters, Satellite
from skyseed_v2.emulator.seedemu import SkyseedAutonomousSystem
from skyseed_v2.emulator_to_simulator import create_simulator_static_input
from skyseed_v2.shared import NodeId


def build_rendered_emulator():
    emulator = Emulator()
    base = Base()
    satellite_as = SkyseedAutonomousSystem(65000)
    base.setAutonomousSystem(satellite_as)
    shell = satellite_as.create_shell("shell-1")
    orbit = shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    orbit.create_satellites(2, satellite_id_prefix="satellite")
    emulator.addLayer(base)
    emulator.render()
    return emulator, satellite_as, shell, orbit


def test_orbit_and_as_inventory_share_registered_extension_nodes() -> None:
    emulator, satellite_as, _, orbit = build_rendered_emulator()
    registered = emulator.getRegistry().getByType("65000", "extnode")

    assert registered == list(orbit.satellites)
    assert all(isinstance(satellite, Satellite) for satellite in registered)
    assert all(isinstance(satellite, ExtensionNode) for satellite in registered)
    assert all(
        satellite_as.getExtensionNode(satellite.satellite_id) is satellite
        for satellite in orbit.satellites
    )
    assert satellite_as.getExtensionNodes() == [
        "satellite-0",
        "satellite-1",
    ]
    assert all(
        satellite.getRole() is NodeRole.ExtensionNode
        for satellite in registered
    )
    assert emulator.getRegistry().getByType("65000", "satnode") == []
    assert emulator.getRegistry().getByType("65000", "hnode") == []


def test_only_satellites_not_shell_or_orbit_enter_registry() -> None:
    emulator, _, shell, orbit = build_rendered_emulator()
    registered_objects = tuple(emulator.getRegistry().getAll().values())

    assert shell not in registered_objects
    assert orbit not in registered_objects
    assert all(satellite in registered_objects for satellite in orbit.satellites)


def test_standard_base_render_and_options_apply_to_satellite_instances() -> None:
    emulator, _, _, orbit = build_rendered_emulator()
    nodes = emulator.getRegistry().getByType("65000", "extnode")

    for registered, satellite in zip(nodes, orbit.satellites, strict=True):
        assert registered is satellite
        assert satellite.getInterfaces() == []
        assert satellite.getOption("sysctl_netipv4_ip_forward") is not None
        assert satellite.getOption("sysctl_netipv4_conf_rp_filter") is not None
        files = {file.get()[0]: file.get()[1] for file in satellite.getFiles()}
        assert files["/ifinfo.txt"] == ""
        assert "/interface_setup" in files


def test_satellite_identity_is_projected_with_public_node_labels() -> None:
    _, _, _, orbit = build_rendered_emulator()
    satellite = orbit.satellites[0]

    assert satellite.getLabel() == {
        "skyseed.entity_type": "Satellite",
        "skyseed.satellite_id": "satellite-0",
        "skyseed.shell_id": "shell-1",
        "skyseed.orbit_id": "orbit-1",
    }


def test_simulation_input_is_a_static_copy_of_registered_satellite() -> None:
    emulator, satellite_as, _, _ = build_rendered_emulator()

    for simulation_input in create_simulator_static_input(
        (satellite_as,)
    ).list_satellites():
        satellite = satellite_as.get_satellite(simulation_input.satellite_id)
        registered = emulator.getRegistry().get(
            "65000", "extnode", simulation_input.satellite_id
        )
        assert registered is satellite
        assert simulation_input.node_id == NodeId(
            asn=satellite.getAsn(),
            entity_kind="satellite",
            entity_id=satellite.satellite_id,
        )
        assert "extnode" not in simulation_input.node_id.value


def test_as_rejects_duplicate_shell_and_satellite_ids_across_shells() -> None:
    satellite_as = SkyseedAutonomousSystem(65000)
    first_shell = satellite_as.create_shell("shell-1")
    with pytest.raises(ValueError, match="shell_id"):
        satellite_as.create_shell("shell-1")

    second_shell = satellite_as.create_shell("shell-2")
    first_orbit = first_shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 15.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    second_orbit = second_shell.create_orbit(
        "orbit-1",
        parameters=OrbitParameters(
            550.0, 53.0, 25.0, datetime(2026, 1, 1, tzinfo=timezone.utc)
        ),
    )
    first_orbit.create_satellite(
        "satellite-0", orbit_index=0, mean_anomaly_deg=0.0
    )

    with pytest.raises(ValueError, match="satellite_id"):
        second_orbit.create_satellite(
            "satellite-0", orbit_index=0, mean_anomaly_deg=180.0
        )
