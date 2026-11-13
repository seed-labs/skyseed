"""Context cleanup must preserve a deployment/run failure."""
import pytest
from skyseed import Deployment


@pytest.mark.parametrize('fail_during_start', [True, False])
def test_context_retains_primary_failure_and_attempts_cleanup(tmp_path, fail_during_start):
    class FailingDeployment(Deployment):
        cleaned = False

        def up(self):
            if fail_during_start:
                raise ValueError('partial deployment')

        def down(self):
            self.cleaned = True
            raise RuntimeError('cleanup problem')

    deployment = FailingDeployment(tmp_path)
    with pytest.raises(ValueError) as result:
        with deployment:
            raise ValueError('experiment failed')
    assert deployment.cleaned
    assert 'cleanup problem' in result.value.__notes__[0]
    assert str(result.value) == ('partial deployment' if fail_during_start else 'experiment failed')
