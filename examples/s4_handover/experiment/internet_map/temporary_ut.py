"""Bridge only this example's temporary UT files into the map's display data.

Replace this bridge alongside the temporary UT implementation when integrating a
real terminal system. These files and fields are not a SkySEED UT interface.
"""
import json
import subprocess


def display_terminal(scene, run_output, position):
    experiment = scene.data["experiment"]
    terminal = json.loads((run_output / "terminal.json").read_text())
    container = json.loads(subprocess.check_output(["docker", "inspect", terminal["ut_container"]]))[0]
    node = {
        "name": "UT", "role": "UT", "group": "UT", **position,
        "location": f"{experiment['ut_latitude_deg']:.4f}°, {experiment['ut_longitude_deg']:.4f}°",
        "identity_address": experiment["ut_address"], "zone": experiment["zone"],
        "ground_stations": terminal["ground_ips"],
        "container": container["Id"], "container_name": container["Name"].lstrip("/"),
        "pid": container["State"]["Pid"],
    }
    attachment = {"node": "ut", "interface": "ssut", "peer_interface": "ssut",
                  "state_file": str(run_output / "attachment-state.json")}
    return node, attachment
