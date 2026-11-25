"""S4 navigation for the saved scene and plans."""
from pathlib import Path
from html import escape
from skyseed import Scene


def write_index(output):
    """Write only the offline plan links; network observation is interactive."""
    template = Path(__file__).with_name("index.html").read_text()
    scene = Scene.load(output / "scene/scene.scene")
    title = f"{len(scene.get_satellites())} satellites / {len(scene.get_ground_stations())} GS"
    template = template.replace('<!--COUNTS-->', title)
    template = template.replace('<!--SHELLS-->', escape(' + '.join(shell.group_id for shell in scene.get_shells())))
    (output / "index.html").write_text(template)
