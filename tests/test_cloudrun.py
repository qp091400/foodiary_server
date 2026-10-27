import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.ocr.paddle import PaddleOcrEngine
from scripts import cloudrun, cloudrun_entrypoint
from scripts.estimate_cloudrun import estimate
from tests.test_production import production_settings


def config():
    return dict(project="example-project", project_number="123456789012", region="asia-northeast1",
                service="foodiary", repository="foodiary", bucket="example-project-foodiary-uploads",
                origins=["https://app.example.com"], smtp_host="smtp.gmail.com",
                smtp_user="mail@example.com", mail_from="mail@example.com")


def prepare_deployment(tmp_path):
    deployment = cloudrun.Deployment(config(), tmp_path)
    image = "asia-northeast1-docker.pkg.dev/example-project/foodiary/foodiary@sha256:" + "a" * 64
    (tmp_path / "image.txt").write_text(image)
    (tmp_path / "versions.json").write_text(json.dumps(dict.fromkeys(cloudrun.SECRET_KEYS, "3")))
    return deployment, image


def test_neon_connection_preserves_encoded_password_and_enforces_tls():
    value = "postgresql://owner:" + "a" * 24 + "%40%25%2F@db.example.com/foodiary?channel_binding=require"
    result = cloudrun.normalize_database_url(value)
    assert result.startswith("postgresql+psycopg://owner:" + "a" * 24 + "%40%25%2F@")
    assert parse_qs(urlsplit(result).query) == {
        "channel_binding": ["require"], "sslmode": ["verify-full"], "sslrootcert": ["system"], "connect_timeout": ["10"],
    }


@pytest.mark.parametrize("value", ["not-a-url", "sqlite://", "postgresql://owner:short@db/foodiary",
                                     "postgresql://owner:" + "s" * 30 + "@db/foodiary?sslmode=disable"])
def test_invalid_connection_errors_do_not_echo_secret(value):
    with pytest.raises(ValueError) as error:
        cloudrun.normalize_database_url(value)
    assert value not in str(error.value)


def test_plan_is_offline_and_contains_no_secret_values(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudrun.subprocess, "run", lambda *a, **kw: pytest.fail("Plan must not run commands"))
    plan = cloudrun.Deployment(config(), tmp_path).plan()
    assert (plan["cpu"], plan["memory"], plan["min_instances"], plan["max_instances"]) == (2, "8Gi", 0, 1)
    env = cloudrun.environment(config())
    assert not set(cloudrun.SECRET_KEYS) & env.keys()
    assert "*" not in env["TRUSTED_HOSTS"] and env["RATE_LIMIT_SCOPE"] == "global"


def test_setup_checks_project_number_before_any_mutation(tmp_path, monkeypatch):
    deployment = cloudrun.Deployment(config(), tmp_path)
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        return "999999999999"
    monkeypatch.setattr(deployment, "gcloud", fake)
    with pytest.raises(RuntimeError, match="does not match"):
        deployment.setup()
    assert calls == [("projects", "describe", "example-project", "--format=value(projectNumber)")]


def test_migrate_and_deploy_use_pinned_secrets_and_separate_identities(tmp_path, monkeypatch):
    deployment, image = prepare_deployment(tmp_path)
    calls = []
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="migration job"):
        deployment.deploy()
    assert calls == []
    deployment.migrate()
    migration = calls[0]
    assert "--command=alembic" in migration and "--max-retries=0" in migration
    assert "--set-secrets=DATABASE_URL=foodiary-database-url:3" in migration
    assert "--service-account=foodiary-migrate@example-project.iam.gserviceaccount.com" in migration
    assert calls[1][:4] == ("run", "jobs", "execute", "foodiary-migrate")
    assert json.loads((tmp_path / "migration.json").read_text())["image"] == image
    deployment.deploy()
    command = calls[-1]
    assert "--image=" + image in command
    assert "--no-allow-unauthenticated" in command and "--cpu-throttling" in command
    assert "--cpu-boost" in command  # 잠든 서버가 깨어날 때(모델 로딩) CPU를 더 받는다
    assert "--memory=8Gi" in command and "--max=1" in command and "--min=0" in command
    assert "--service-account=foodiary-run@example-project.iam.gserviceaccount.com" in command
    assert any("mount-options=uid=10001;gid=10001" in argument for argument in command)


def test_failed_migration_never_marks_image_as_migrated(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("job failed")
    monkeypatch.setattr(deployment, "gcloud", fail)
    with pytest.raises(RuntimeError):
        deployment.migrate()
    assert not (tmp_path / "migration.json").exists()


def test_changing_database_secret_requires_migrating_that_database(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: None)
    deployment.migrate()
    versions = json.loads((tmp_path / "versions.json").read_text())
    versions["DATABASE_URL"] = "4"
    (tmp_path / "versions.json").write_text(json.dumps(versions))
    with pytest.raises(ValueError, match="migration job"):
        deployment.deploy()


def test_test_password_is_optional_and_only_bound_when_set(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("secrets", "describe"):
            return None  # the secret does not exist yet
        return "projects/123456789012/secrets/foodiary-receipt-test-password/versions/7"
    monkeypatch.setattr(deployment, "gcloud", fake)
    deployment.migrate()
    deployment.deploy()
    assert not any("RECEIPT_TEST_PASSWORD" in argument for argument in calls[-1])

    # Too short, or the repeat differs: nothing is created or recorded.
    calls.clear()
    for answers in (["too-short"], ["long-enough-pass", "different-pass!"]):
        replies = iter(answers)
        monkeypatch.setattr(cloudrun.getpass, "getpass", lambda prompt: next(replies))
        with pytest.raises(ValueError, match="Nothing was changed"):
            deployment.set_test_password()
    assert calls == [] and not deployment.test_page

    replies = iter(["long-enough-pass", "long-enough-pass"])
    monkeypatch.setattr(cloudrun.getpass, "getpass", lambda prompt: next(replies))
    deployment.set_test_password()
    assert (tmp_path / "secrets" / "RECEIPT_TEST_PASSWORD").read_text() == "long-enough-pass"
    assert ("secrets", "create", "foodiary-receipt-test-password", "--replication-policy=automatic") in calls
    assert any(c[:2] == ("secrets", "add-iam-policy-binding")
               and "--member=serviceAccount:foodiary-run@example-project.iam.gserviceaccount.com" in c for c in calls)
    assert not any("long-enough-pass" in argument for call in calls for argument in call)
    deployment.deploy()
    assert any(argument.endswith(",RECEIPT_TEST_PASSWORD=foodiary-receipt-test-password:7") for argument in calls[-1])

    # Re-uploading the required secrets keeps the test password; --disable drops it at the next deploy.
    for key in cloudrun.SECRET_KEYS:
        (tmp_path / "secrets" / key).write_text("x" * 48)
    monkeypatch.setattr(cloudrun, "normalize_database_url", lambda value: value)
    deployment.upload_secrets()
    assert deployment.versions()["RECEIPT_TEST_PASSWORD"] == "7"
    deployment.set_test_password(disable=True)
    assert not deployment.test_page


class FakeResponse(io.BytesIO):
    def __init__(self, status):
        super().__init__(b'{"ocr": "ready"}')
        self.status = status
        self.headers = {"X-Content-Type-Options": "nosniff", "Strict-Transport-Security": "max-age=31536000"}


def test_verify_expects_the_test_page_only_with_a_test_password(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    monkeypatch.setattr(deployment, "gcloud", lambda *a, **k: "identity-token")
    served = {"/ready": 200, "/fridges": 401}
    requests = []
    def fake_urlopen(request, timeout):
        path = urlsplit(request.full_url).path
        requests.append((request.get_method(), path))
        return FakeResponse(served.get(path, 404))
    monkeypatch.setattr(cloudrun, "urlopen", fake_urlopen)

    deployment.verify()  # no test password: the page must be missing
    assert ("GET", "/receipt-test") in requests and ("POST", "/receipt-test") in requests

    versions = deployment.versions() | {"RECEIPT_TEST_PASSWORD": "1"}
    (tmp_path / "versions.json").write_text(json.dumps(versions))
    with pytest.raises(RuntimeError, match="GET /receipt-test: expected HTTP 200, received 404"):
        deployment.verify()
    served["/receipt-test"] = 200  # a page that answered POST without a password with 200 would fail too
    with pytest.raises(RuntimeError, match="POST /receipt-test: expected HTTP 401, received 200"):
        deployment.verify()


def test_publish_stops_if_private_checks_fail(tmp_path, monkeypatch):
    deployment = cloudrun.Deployment(config(), tmp_path)
    monkeypatch.setattr(deployment, "gcloud", lambda *a, **k: pytest.fail("Must not change IAM"))
    def fail():
        raise RuntimeError("not ready")
    monkeypatch.setattr(deployment, "verify", fail)
    with pytest.raises(RuntimeError, match="not ready"):
        deployment.publish()


def test_cloudrun_uses_global_limit_across_clients_and_ignores_spoofed_forwarding(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_scope", "global")
    monkeypatch.setattr(settings, "auth_requests_per_minute", 1)
    response = client.post("/auth/login", json={"username": "missing", "password": "invalid"},
                           headers={"X-Forwarded-For": "1.2.3.4"})
    assert response.status_code == 401
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    # Different network peer and forged header still share the instance-wide limit.
    other = TestClient(app, client=("198.51.100.2", 50000))
    assert other.post("/auth/login", json={"username": "missing", "password": "invalid"},
                      headers={"X-Forwarded-For": "9.8.7.6"}).status_code == 429


def test_runtime_refuses_ephemeral_upload_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudrun_entrypoint, "settings", production_settings(
        upload_dir=str(tmp_path), ocr_models_dir=str(tmp_path), rate_limit_scope="global"))
    monkeypatch.setattr(cloudrun_entrypoint, "verify_model_files", lambda: {})
    with pytest.raises(RuntimeError, match="persistent Cloud Storage volume"):
        cloudrun_entrypoint.check_runtime()


def test_runtime_checks_writable_volume_and_cleans_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudrun_entrypoint, "settings", production_settings(
        upload_dir=str(tmp_path), ocr_models_dir=str(tmp_path), rate_limit_scope="global"))
    monkeypatch.setattr(cloudrun_entrypoint, "verify_model_files", lambda: {})
    monkeypatch.setattr(Path, "is_mount", lambda self: True)
    cloudrun_entrypoint.check_runtime()
    assert list(tmp_path.iterdir()) == []


def test_missing_baked_models_fail_before_constructing_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ocr_models_dir", str(tmp_path))
    with pytest.raises(RuntimeError, match="model file is missing"):
        PaddleOcrEngine("korean")._load()


def test_cost_estimate_does_not_assume_ten_dollars_caps_the_bill():
    assert estimate(30_000, 1000)["cloud_run_usd"] == 0
    assert estimate(300_000, 10_000)["after_pro_credit_usd"] == pytest.approx(5.18)
    assert estimate(0, 0, other_costs=12)["after_pro_credit_usd"] == 2
    assert estimate(100, 1, free_tier=False)["cloud_run_usd"] == pytest.approx(0.0068)
