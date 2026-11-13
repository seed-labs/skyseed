"""Render saved build artifacts independently of planning or network execution."""
from pathlib import Path
import webbrowser

from tools.observation.plan_views import (
    load_context, write_scene, write_physical, write_link, write_network, write_compiler,
)
from .experiment.observation.index import write_index


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    scene = output / "scene/scene.scene"
    logical = output / "plans/logical"
    context_file = output / "observations/plans.json"
    views = output / "views"

    write_scene(scene, views / "scene")
    context = load_context(context_file)
    write_physical(context, views / "physical")
    write_link(scene, logical / "base_link.plan", logical / "execution_link.plan",
               context=context, output=views / "link")
    write_network(scene, logical / "base_route.plan", logical / "delivery_route.plan",
                  logical / "base_link.plan", context=context, output=views / "network")
    # The compiler view needs only its plan, so release the shared drawing data.
    del context
    write_compiler(output / "plans/physical/bpf.plan", views / "compiler")
    write_index(output)
    webbrowser.open((output / "index.html").resolve().as_uri())
