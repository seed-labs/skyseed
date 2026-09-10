"""Assemble all five existing Shell definitions into one static Emulator input."""
from pathlib import Path
import shutil
from yaml import safe_dump, safe_load

from skyseed_v2.configuration.emulator import load_static_emulator_config
from skyseed_v2.emulator import create_static_emulator

SHARED_INPUTS = Path(__file__).resolve().parents[1] / "starlink_gen1_satellite_internet"


def write_inputs(output: Path, settings: dict) -> Path:
    inputs = output / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for name in ("shells.yaml", "ground_stations.yaml", "zones.yaml"):
        shutil.copyfile(SHARED_INPUTS / name, inputs / name)
    shells = safe_load((inputs / "shells.yaml").read_text())["shells"]
    selected = settings["shells"]
    if selected != ["S1", "S2", "S3", "S4", "S5"]:
        raise ValueError("this example requires the five shared S1–S5 definitions")
    if {shell["shell_id"] for shell in shells} != set(selected):
        raise ValueError("shared Shell inventory differs")
    # Inline Shells already belong to the accepted static input schema. Only
    # this small composition step differs from the single selected-Shell example.
    scenario = {"schema_version": "skyseed-static-emulator/v1", "skyseed_autonomous_systems": [
        {"asn": 65040, "shells": shells, "ground_stations_file": "ground_stations.yaml"}]}
    path = inputs / "scenario.yaml"
    path.write_text(safe_dump(scenario, sort_keys=False))
    (inputs / "delivery-settings.yaml").write_text(safe_dump(settings, sort_keys=False))
    return path


def build_emulator(scenario: Path):
    return create_static_emulator(load_static_emulator_config(scenario))
