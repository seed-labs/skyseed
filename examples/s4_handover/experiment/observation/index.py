"""S4 navigation joins standard plan views and example-specific results."""
import html
from pathlib import Path
from skyseed import Scene


def write_index(output):
    """Each run keeps its own pages; rebuilding plans does not replace results."""
    links = []
    for run in sorted((output / "runs").glob("*")):
        if not (run / "views/results/index.html").exists():
            continue
        relative = run.relative_to(output).as_posix()
        links.append(f'<li>{html.escape(run.name)}<br>'
                     f'<a href="{relative}/views/replay/index.html">3D 通信回放</a> · '
                     f'<a href="{relative}/views/results/index.html">流量统计与证据</a></li>')
    content = '<ul>' + ''.join(links) + '</ul>' if links else '<p>运行后生成 / available after Run</p>'
    template = Path(__file__).with_name("index.html").read_text()
    scene = Scene.load(output / "scene/scene.scene")
    title = f"{len(scene.get_satellites())} satellites / {len(scene.get_ground_stations())} GS"
    template = template.replace('<!--COUNTS-->', title)
    (output / "index.html").write_text(template.replace('<!--RUNS-->', content))
