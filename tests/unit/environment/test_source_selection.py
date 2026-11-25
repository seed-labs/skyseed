from pathlib import Path
import os
import runpy
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]


def run_python(args, cwd, pythonpath=None):
    env = dict(os.environ)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def test_selected_checkout_is_used_outside_repository(tmp_path):
    result = run_python([str(ROOT / "scripts/check_dev_environment.py")], tmp_path)
    assert result.returncode == 0, result.stderr
    assert str(ROOT / "src/skyseed/__init__.py") in result.stdout


def test_other_installation_is_not_silently_used(tmp_path):
    package = tmp_path / "skyseed"
    package.mkdir()
    (package / "__init__.py").write_text("raise AssertionError('should not be imported')")
    result = run_python([str(ROOT / "scripts/check_active_source.py")], tmp_path, tmp_path)
    assert result.returncode != 0 and "Wrong SkySEED source" in result.stderr
    assert "should not be imported" not in result.stderr


def test_mixed_submodule_source_is_rejected(monkeypatch, tmp_path):
    verify = runpy.run_path(str(ROOT / "scripts/check_active_source.py"))["verify_source"]
    foreign = ModuleType("skyseed.foreign")
    foreign.__file__ = str(tmp_path / "old.py")
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    with pytest.raises(RuntimeError, match="Mixed SkySEED source"):
        verify()


def test_example_subprocess_uses_installed_package(tmp_path):
    saved = tmp_path / "tiny.scene"
    result = run_python([str(ROOT / "examples/tiny_network/scene.py"), str(saved)], tmp_path)
    assert result.returncode == 0, result.stderr
    from skyseed import Scene
    assert len(Scene.load(saved).nodes) == 3
