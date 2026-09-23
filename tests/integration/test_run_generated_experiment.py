"""Generated-control orchestration and real lightweight interruption ordering."""
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import pytest

from skyseed.experiment import execution, run_generated_experiment


def control(output, name, body=""):
    output.mkdir(exist_ok=True)
    path = output / name
    path.write_text(
        f"#!{sys.executable}\n"
        "import os, signal, sys, time\nfrom pathlib import Path\n"
        "root = Path(__file__).parent\n"
        "def record(value):\n"
        "    with (root / 'events').open('a') as stream:\n"
        "        stream.write(value + '\\n')\n"
        f"record({name!r})\n" + body + "\n"
    )
    path.chmod(0o755)
    return path


def events(output):
    path = output / "events"
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.parametrize("start,run,teardown,cleanup,expected", [
    (0, 0, 0, True, ["deploy.sh", "run.sh", "cleanup.sh"]),
    (7, 0, 0, True, ["deploy.sh", "cleanup.sh"]),
    (0, 8, 0, True, ["deploy.sh", "run.sh", "cleanup.sh"]),
    (0, 0, 9, True, ["deploy.sh", "run.sh", "cleanup.sh"]),
    (7, 0, 9, True, ["deploy.sh", "cleanup.sh"]),
    (0, 8, 9, True, ["deploy.sh", "run.sh", "cleanup.sh"]),
    (0, 0, 0, False, ["deploy.sh", "run.sh"]),
    (7, 0, 0, False, ["deploy.sh"]),
    (0, 8, 0, False, ["deploy.sh", "run.sh"]),
])
def test_control_order_and_failure_precedence(tmp_path, start, run, teardown, cleanup, expected):
    output = tmp_path / "generated output"
    control(output, "deploy.sh", f"sys.exit({start})")
    control(output, "run.sh", f"sys.exit({run})")
    if cleanup:
        control(output, "cleanup.sh", f"sys.exit({teardown})")
    primary = start or run or (teardown if cleanup else 0)
    if primary:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            run_generated_experiment(output, cleanup=cleanup)
        assert caught.value.returncode == primary
        expected_script = "deploy.sh" if start else "run.sh" if run else "cleanup.sh"
        assert caught.value.cmd == [str(output / expected_script)]
        notes = getattr(caught.value, "__notes__", ())
        assert bool(notes) is bool((start or run) and teardown and cleanup)
        if notes:
            assert "teardown also failed" in notes[0] and "9" in notes[0]
    else:
        assert run_generated_experiment(output, cleanup=cleanup) is None
    assert events(output) == expected


@pytest.mark.parametrize("primary", [RuntimeError("runtime failed"), KeyboardInterrupt()])
def test_secondary_cleanup_failure_preserves_original_exception(tmp_path, monkeypatch, primary):
    for name in ("deploy.sh", "run.sh", "cleanup.sh"):
        control(tmp_path, name)
    def fail(script):
        if script.name == "run.sh":
            raise primary
        if script.name == "cleanup.sh":
            raise RuntimeError("teardown failed")
    monkeypatch.setattr(execution, "_run_generated_control", fail)
    with pytest.raises(type(primary)) as caught:
        run_generated_experiment(tmp_path)
    assert caught.value is primary
    assert "RuntimeError: teardown failed" in primary.__notes__[0]


def test_inherited_environment_and_streams_without_extra_verification(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("SKYSEED_BPF_RUNTIME_WORKERS", "3")
    control(tmp_path, "deploy.sh", "print('startup stdout', flush=True)")
    control(tmp_path, "run.sh", "print(os.environ['SKYSEED_BPF_RUNTIME_WORKERS'], file=sys.stderr)")
    control(tmp_path, "cleanup.sh")
    def forbidden(*args):
        raise AssertionError("convenience function repeated artifact verification")
    monkeypatch.setattr(execution, "_read_and_verify_execution_definition", forbidden)
    run_generated_experiment(tmp_path)
    captured = capfd.readouterr()
    assert captured.out == "startup stdout\n"
    assert captured.err == "3\n"


@pytest.mark.parametrize("bad_input", ["path-type", "cleanup-type", "missing-run", "missing-cleanup", "not-executable"])
def test_simple_input_errors_precede_startup(tmp_path, bad_input):
    for name in ("deploy.sh", "run.sh", "cleanup.sh"):
        control(tmp_path, name)
    output, cleanup = tmp_path, True
    expected = TypeError
    if bad_input == "path-type":
        output = str(output)
    elif bad_input == "cleanup-type":
        cleanup = 1
    elif bad_input.startswith("missing"):
        (tmp_path / ("run.sh" if bad_input == "missing-run" else "cleanup.sh")).unlink()
        expected = FileNotFoundError
    else:
        (tmp_path / "run.sh").chmod(0o644)
        expected = PermissionError
    with pytest.raises(expected):
        run_generated_experiment(output, cleanup=cleanup)
    assert events(tmp_path) == []


def test_startup_spawn_failure_still_attempts_teardown(tmp_path):
    script = control(tmp_path, "deploy.sh")
    script.write_text("#!/missing-generated-interpreter\n")
    control(tmp_path, "run.sh")
    control(tmp_path, "cleanup.sh")
    with pytest.raises(FileNotFoundError):
        run_generated_experiment(tmp_path)
    assert events(tmp_path) == ["cleanup.sh"]


def wait_for(output, event, process):
    deadline = time.monotonic() + 15
    while event not in events(output):
        if process.poll() is not None:
            raise AssertionError(process.communicate())
        if time.monotonic() >= deadline:
            raise AssertionError(f"child did not reach {event}: {events(output)}")
        time.sleep(0.01)


@pytest.mark.parametrize("active,cleanup,group_signal,teardown_code", [
    ("deploy.sh", True, False, 0),
    ("run.sh", True, False, 0),
    ("run.sh", True, True, 0),
    ("deploy.sh", False, True, 0),
    ("run.sh", False, False, 0),
    ("run.sh", True, True, 9),
])
def test_ctrl_c_waits_for_shell_and_runtime_drain_before_teardown(
    tmp_path, active, cleanup, group_signal, teardown_code,
):
    for name in ("deploy.sh", "run.sh"):
        control(tmp_path, name)
    control(tmp_path, "cleanup.sh", f"sys.exit({teardown_code})")
    child = control(tmp_path, "active.py", """
try:
    (root / 'child-pid').write_text(str(os.getpid()))
    (root / 'shell-pid').write_text(str(os.getppid()))
    record('ready')
    while True:
        time.sleep(1)
finally:
    record('draining')
    time.sleep(0.4)
    record('drained')
""")
    # Match the existing run.sh shape: shell waits for Python and retains status.
    (tmp_path / active).write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nstatus=0\n"
        f"{shlex.quote(sys.executable)} {shlex.quote(str(child))} || status=$?\n"
        "exit \"$status\"\n"
    )
    driver = control(tmp_path, "driver.py", f"""
from skyseed.experiment import run_generated_experiment
try:
    run_generated_experiment(root, cleanup={cleanup!r})
except KeyboardInterrupt as error:
    record('interruption-returned')
    (root / 'notes.json').write_text(__import__('json').dumps(getattr(error, '__notes__', [])))
else:
    raise AssertionError('interruption was swallowed')
""")
    process = subprocess.Popen([sys.executable, str(driver)], start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(tmp_path, "ready", process)
        (os.killpg if group_signal else os.kill)(process.pid, signal.SIGINT)
        wait_for(tmp_path, "draining", process)
        os.kill(process.pid, signal.SIGINT)  # Repeated Ctrl-C cannot abandon cleanup.
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        recorded = events(tmp_path)
        assert recorded.index("drained") < recorded.index("interruption-returned")
        if cleanup:
            assert recorded.index("drained") < recorded.index("cleanup.sh") < recorded.index("interruption-returned")
        else:
            assert "cleanup.sh" not in recorded
        notes = json.loads((tmp_path / "notes.json").read_text())
        assert bool(notes) is bool(teardown_code and cleanup)
        for name in ("child-pid", "shell-pid"):
            with pytest.raises(ProcessLookupError):
                os.kill(int((tmp_path / name).read_text()), 0)
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGINT)
            process.communicate(timeout=15)



def test_repeated_ctrl_c_waits_for_slow_cooperative_drain(tmp_path):
    control(tmp_path, "deploy.sh")
    control(tmp_path, "cleanup.sh")
    child = control(tmp_path, "active.py", """
try:
    record('ready')
    while True:
        time.sleep(1)
finally:
    record('draining')
    while not (root / 'release-drain').exists():
        time.sleep(0.02)
    record('drained')
""")
    control(tmp_path, "run.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nstatus=0\n"
        f"{shlex.quote(sys.executable)} {shlex.quote(str(child))} || status=$?\n"
        "exit \"$status\"\n"
    )
    driver = control(tmp_path, "driver.py", """
from skyseed.experiment import run_generated_experiment
try:
    run_generated_experiment(root)
except KeyboardInterrupt as error:
    assert not getattr(error, '__notes__', ())
    record('interruption-returned')
else:
    raise AssertionError('interruption was swallowed')
""")
    process = subprocess.Popen([sys.executable, str(driver)], start_new_session=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_for(tmp_path, "ready", process)
        os.kill(process.pid, signal.SIGINT)
        wait_for(tmp_path, "draining", process)
        # The child owns completion. Slower cleanup and additional foreground
        # interrupts must leave it alive and keep Compose teardown pending.
        for _ in range(6):
            os.killpg(process.pid, signal.SIGINT)
            time.sleep(0.2)
            assert process.poll() is None
            assert "cleanup.sh" not in events(tmp_path)
        (tmp_path / "release-drain").touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert events(tmp_path)[-3:] == ["drained", "cleanup.sh", "interruption-returned"]
    finally:
        (tmp_path / "release-drain").touch()
        if process.poll() is None:
            os.kill(process.pid, signal.SIGINT)
            process.communicate(timeout=15)
