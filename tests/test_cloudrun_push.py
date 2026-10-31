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


@pytest.mark.parametrize("extra", [{"push_web_public_key": "not a valid key"}, {"push_expo_enabled": "false"},
    {"push_immediate_enabled": "false"}, {"push_immediate_enabled": True},
    {"push_immediate_enabled": True, "push_web_public_key": "B" + "A" * 86, "push_vapid_secret": "secret:latest"}])
def test_push_deployment_rejects_malformed_configuration(extra):
    with pytest.raises(ValueError):
        cloudrun.validate_config({**config(), **extra})


def test_immediate_push_redeployment_keeps_private_key_as_pinned_secret_reference(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    deployment.config.update(push_web_public_key="B" + "A" * 86, push_immediate_enabled=True,
        push_vapid_secret="foodiary-web-push-vapid:1", push_vapid_subject="https://app.example.com")
    cloudrun.validate_config(deployment.config)
    calls = []
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: calls.append(args))
    deployment.migrate()
    assert not any("PUSH_VAPID_PRIVATE_KEY" in arg for call in calls for arg in call)
    deployment.deploy()
    env = json.loads((tmp_path / "env.json").read_text())
    assert env["PUSH_IMMEDIATE_ENABLED"] == "true"
    assert env["PUSH_VAPID_SUBJECT"] == "https://app.example.com"
    assert "PUSH_VAPID_PRIVATE_KEY" not in env
    assert any("PUSH_VAPID_PRIVATE_KEY=foodiary-web-push-vapid:1" in arg for call in calls for arg in call)
