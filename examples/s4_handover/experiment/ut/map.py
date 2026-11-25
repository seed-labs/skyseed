"""Bridge only this example's temporary UT files into the map's display data.

Replace this bridge alongside the temporary UT implementation when integrating a
real terminal system. These files and fields are not a SkySEED UT interface.
"""
import json
import subprocess

from tools.internet_map.runtime import container_node


def display_terminal(scene, run_output, layout):
    experiment = scene.data["experiment"]
    terminal = json.loads((run_output / "terminal.json").read_text())
    container = json.loads(subprocess.check_output(["docker", "inspect", terminal["ut_container"]]))[0]
    node = {
        "name": "UT", "role": "UT", "group": "UT", **layout["ut"],
        "location": f"{experiment['ut_latitude_deg']:.4f}°, {experiment['ut_longitude_deg']:.4f}°",
        "identity_address": experiment["ut_address"], "zone": experiment["zone"],
        "ground_stations": terminal["ground_ips"],
        **container_node(container),
    }
    attachment = {"node": "ut", "file": "attachment-state.json", "field": "node",
                  "local": {"interface": "ssut", "address": "169.254.254.2/30", "mac": "02:53:00:00:01:02"},
                  "remote": {"interface": "ssut", "address": "169.254.254.1/30", "mac": "02:53:00:00:01:01"}}
    return {"ut": node}, attachment
