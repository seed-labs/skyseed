"""Drain only etcd services created by this test process's generated outputs."""
import pytest


@pytest.fixture(autouse=True)
def _scoped_interaction_services(monkeypatch):
    from skyseed.experiment import interaction_service as service
    created = []
    original = service.start_interaction
    def start(output, **kwargs):
        client = original(output, **kwargs)
        created.append(output)
        return client
    monkeypatch.setattr(service, 'start_interaction', start)
    yield
    for output in reversed(created):
        try:
            service.stop_interaction(output)
        except FileNotFoundError:
            pass
