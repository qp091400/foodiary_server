import json
import pytest
from scripts import cloudrun
from tests.test_cloudrun import config, prepare_deployment


def test_redeployment_preserves_public_push_config_and_updates_worker_to_same_image(tmp_path, monkeypatch):
    deployment, image = prepare_deployment(tmp_path)
    key = "B" + "A" * 86
    deployment.config.update(push_web_public_key=key, push_expo_enabled=False)
    calls = []
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: calls.append(args))
    deployment.migrate()
    deployment.deploy()
    env = json.loads((tmp_path / "env.json").read_text())
    assert env["PUSH_WEB_PUBLIC_KEY"] == key
    assert env["PUSH_EXPO_ENABLED"] == "false"
    assert "PUSH_VAPID_PRIVATE_KEY" not in env
    assert calls[-1][:4] == ("run", "jobs", "update", "foodiary-push")
    assert "--image=" + image in calls[-1]


@pytest.mark.parametrize("extra", [{"push_web_public_key": "not a valid key"}, {"push_expo_enabled": "false"}])
def test_push_deployment_rejects_malformed_configuration(extra):
    with pytest.raises(ValueError):
        cloudrun.validate_config({**config(), **extra})
