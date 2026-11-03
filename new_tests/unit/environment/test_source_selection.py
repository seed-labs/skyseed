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
    assert str(ROOT / "new_src/skyseed/__init__.py") in result.stdout


@pytest.mark.parametrize("entry,message", [
    ("scripts/check_dev_environment.py", "Wrong SkySEED source"),
    ("new_examples/tiny_network/scene.py", "legacy src/skyseed"),
])
def test_old_source_is_rejected_by_dev_check_or_retired_package_entry(tmp_path, entry, message):
    result = run_python([str(ROOT / entry), str(tmp_path / "unused.scene")], tmp_path, ROOT / "src")
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "unused.scene").exists()


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


def test_explicit_legacy_import_and_test_collection_fail(tmp_path):
    imported = run_python(["-c", "import skyseed"], tmp_path, ROOT / "src")
    assert imported.returncode != 0 and "legacy src/skyseed" in imported.stderr
    collected = run_python(["-m", "pytest", "--collect-only", str(ROOT / "tests")], ROOT)
    assert collected.returncode != 0
    assert "Legacy tests/ is inactive" in collected.stdout + collected.stderr
