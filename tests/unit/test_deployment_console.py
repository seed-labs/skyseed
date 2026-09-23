"""Deployment CLI feedback follows the cleanup result, retaining error semantics."""
import sys
import pytest
from skyseed.deployment import __main__ as cli


@pytest.mark.parametrize('failure', [None, RuntimeError('cleanup failed'), KeyboardInterrupt()])
def test_deployment_cleanup_feedback(tmp_path, monkeypatch, capsys, failure):
    primary = failure
    class Deployer:
        def __init__(self, output):
            assert output == tmp_path
        def cleanup(self):
            feedback = capsys.readouterr()
            assert not feedback.out
            assert 'Deployment cleanup starting' in feedback.err
            assert 'Deployment cleanup succeeded' not in feedback.err
            if primary is not None:
                raise primary
    monkeypatch.setattr(cli, 'Deployer', Deployer)
    monkeypatch.setattr(sys, 'argv', ['cleanup', str(tmp_path), 'cleanup'])
    if failure is None:
        assert cli.main() is None
    else:
        with pytest.raises(type(failure)) as caught:
            cli.main()
        assert caught.value is failure
    feedback = capsys.readouterr()
    assert not feedback.out
    assert ('Deployment cleanup succeeded' in feedback.err) is (failure is None)
    assert ('Deployment cleanup failed' in feedback.err) is (failure is not None)


def test_cleanup_claim_failure_cannot_report_success(tmp_path, monkeypatch, capsys):
    from skyseed.experiment.output.occupancy import claim_runtime
    monkeypatch.setattr(sys, 'argv', ['cleanup', str(tmp_path), 'cleanup'])
    with claim_runtime(tmp_path):
        with pytest.raises(RuntimeError, match='runtime is active'):
            cli.main()
    feedback = capsys.readouterr()
    assert not feedback.out
    assert 'Deployment cleanup starting' in feedback.err
    assert 'Deployment cleanup failed' in feedback.err
    assert 'Deployment cleanup succeeded' not in feedback.err


@pytest.mark.parametrize('code', [0, 7])
@pytest.mark.parametrize('display', [False, True])
def test_mutation_real_child_output_and_error_are_preserved(capfd, display, code):
    from skyseed.deployment.deployer import _mutation, DeploymentError
    command = [sys.executable, '-c', f"import sys; print('native output', flush=True); print('native diagnostic', file=sys.stderr, flush=True); sys.exit({code})"]
    if code:
        with pytest.raises(DeploymentError, match='exit 7' if display else 'native diagnostic'):
            _mutation(command, display_output=display)
    else:
        result = _mutation(command, display_output=display)
        assert result.stdout == ('' if display else 'native output\n')
    captured = capfd.readouterr()
    assert captured.out == ('native output\n' if display else '')
    assert captured.err == ('native diagnostic\n' if display else '')


def test_startup_inherits_terminal_descriptors(monkeypatch):
    from skyseed.deployment import deployer
    class Client:
        returncode = 0
        def __init__(self, command, **kw):
            assert kw == dict(start_new_session=True, stdout=None, stderr=None, text=True)
        def communicate(self): return None, None
    class Guard:
        def record(self, *a, **kw): pass
    monkeypatch.setattr(deployer, 'Popen', Client)
    for command in (['docker', 'pull', 'image'], ['docker', 'compose', 'build'], ['docker', 'compose', 'up']):
        deployer._startup_command(command, Guard())


def test_mutation_preserves_real_terminal_detection():
    import os
    import subprocess
    master, terminal = os.openpty()
    try:
        result = subprocess.run([sys.executable, '-c',
            "import sys; from skyseed.deployment.deployer import _mutation; "
            "_mutation([sys.executable, '-c', 'import sys; print(sys.stdout.isatty(), sys.stderr.isatty(), flush=True)'], display_output=True)"],
            stdout=terminal, stderr=terminal)
        assert result.returncode == 0
        assert os.read(master, 4096).strip() == b'True True'
    finally:
        os.close(terminal)
        os.close(master)
