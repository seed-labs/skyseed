"""A new wheel must not contain modules left in the retired build cache."""
from pathlib import Path
from configparser import ConfigParser
import shutil
import subprocess
import sys
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[3]


def test_wheel_excludes_retired_build_cache(tmp_path):
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    source = metadata["tool"]["setuptools"]["packages"]["find"]["where"][0]
    project = tmp_path / "project"
    project.mkdir()
    for name in ("pyproject.toml", "setup.cfg", "setup.py", "README.md"):
        shutil.copyfile(ROOT / name, project / name)
    shutil.copytree(ROOT / source / "skyseed", project / source / "skyseed",
                    ignore=shutil.ignore_patterns("__pycache__"))
    stale = project / "build/lib/skyseed/simulator"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text("raise AssertionError('retired implementation')")
    config = ConfigParser()
    config.read(project / "setup.cfg")
    # Also seed removed files inside the active build cache: a separate build
    # root alone cannot handle later module renames within that same root.
    active_cache = project / config["build"]["build_base"] / "lib/skyseed/scene"
    active_cache.mkdir(parents=True)
    (active_cache / "records.py").write_text("raise AssertionError('removed module')")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(tmp_path / "wheels"), str(project)],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next((tmp_path / "wheels").glob("skyseed-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert not any(name.startswith("skyseed/simulator/") for name in names)
        assert "skyseed/scene/scene.py" in names
        assert "skyseed/emulator/sky_as.py" in names
        assert "skyseed/scene/README.md" in names
        expected = {str(path.relative_to(project / source))
                    for path in (project / source / "skyseed").rglob("*")
                    if path.is_file() and path.suffix in {".py", ".md"}}
        assert {name for name in names if name.startswith("skyseed/")} == expected
        # Import from the actual distributable in a fresh isolated process.
        code = """
import sys
sys.path.insert(0, sys.argv[1])
import skyseed
from skyseed import Scene, SkyBase
assert skyseed.__file__.startswith(sys.argv[1])
scene = Scene()
scene.create_ground_station('G1', latitude_deg=40, longitude_deg=116)
base = SkyBase()
autonomous_system = base.createSkyAutonomousSystem(150)
autonomous_system.loadScene(scene)
assert autonomous_system.getNode('G1').getAsn() == 150
"""
        imported = subprocess.run([sys.executable, "-I", "-c", code, str(wheel)],
                                  cwd=tmp_path, capture_output=True, text=True)
        assert imported.returncode == 0, imported.stdout + imported.stderr
