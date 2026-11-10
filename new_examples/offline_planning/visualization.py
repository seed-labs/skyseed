"""Compose existing inspectors around this example's actual inputs and results."""
from itertools import chain
from pathlib import Path
import shutil

from skyseed import load_plans
from tools.observation.scene import inspection as scene_view
from tools.observation.physical import inspection as physical_view
from tools.observation.link import inspection as link_view
from tools.observation.network import inspection as network_view
from tools.observation.compiler import inspection as compiler_view


def write_visualizations(output, pc, ip_network, link_observations, route_observations, ip_observations):
    physical = pc.get_layer("physical-layer")
    link = pc.get_layer("link-layer")
    network = pc.get_layer("network-layer")
    scene_view.write_inspection(scene_view.inspection_data(pc.scene), output / "scene")
    # Physical is an explicit geometry query view. It does not rerun link or route planning.
    timestamps = [observation.timestamp for observation in link_observations]
    physical_view.write_inspection(physical_view.inspection_data(physical, timestamps), output / "physical")
    data = link_view.inspection_data(
        physical, link.base_plans, link.execution_plans, evaluations=link_observations,
    )
    data["plan_details"] = "plans/index.html"
    link_view.write_inspection(data, output / "link")
    for directory, layer, observations in (
        ("network", network, route_observations),
        ("network-ip", ip_network, ip_observations),
    ):
        data = network_view.inspection_data(
            physical, layer.base_plans, layer.delivery_plans, link.base_plans,
            evaluations=observations,
        )
        data["plan_details"] = "plans/index.html"
        network_view.write_inspection(data, output / directory)
    compiler_view.write_inspection(chain(
        load_plans(output / "compiler/bpf.plan"), load_plans(output / "compiler/linux.plan"),
    ), output / "compiler")
    shutil.copyfile(Path(__file__).with_name("index.html"), output / "index.html")
