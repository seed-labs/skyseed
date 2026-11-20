"""Render saved build artifacts independently of planning or network execution."""
from pathlib import Path
import webbrowser

from tools.observation.plan_views import (
    load_context, write_scene, write_physical, write_link, write_network, write_compiler,
)
from experiment.observation.index import write_index


if __name__ == "__main__":
    output = Path(__file__).parent / "output"
    scene = output / "scene/scene.scene"
    logical = output / "plans/logical"
    context_file = output / "observations/plans.json"
    views = output / "views"

    print("[1/5] Writing Scene view...", flush=True)
    write_scene(scene, views / "scene")
    print("[2/5] Loading observations and writing Physical view...", flush=True)
    context = load_context(context_file)
    write_physical(context, views / "physical")
    print("[3/5] Writing Link views...", flush=True)
    write_link(scene, logical / "base_link.plan", logical / "execution_link.plan",
               context=context, output=views / "link")
    print("[4/5] Writing Network views...", flush=True)
    write_network(scene, logical / "base_route.plan", logical / "delivery_route.plan",
                  logical / "base_link.plan", context=context, output=views / "network")
    # The compiler view needs only its plan, so release the shared drawing data.
    del context
    print("[5/5] Writing Compiler views...", flush=True)
    write_compiler(output / "plans/physical/bpf.plan", views / "compiler")
    write_index(output)
    print(f"Views ready: {output / 'index.html'}", flush=True)
    webbrowser.open((output / "index.html").resolve().as_uri())
