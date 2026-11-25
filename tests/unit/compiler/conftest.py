import pytest
import yaml


@pytest.fixture
def deployment(tmp_path):
    prefix = "org.seedsecuritylabs.seedemu.meta.skyseed."
    services = {node: {"labels": {prefix + "scene_id": node, prefix + "scene_kind": "satellite"},
                       "network_mode": "none"} for node in ("a", "b", "c")}
    path = tmp_path / "deployment"
    path.mkdir()
    (path / "docker-compose.yml").write_text(yaml.safe_dump({"services": services}))
    return path
