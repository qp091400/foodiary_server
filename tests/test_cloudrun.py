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
    assert (plan["cpu"], plan["memory"], plan["min_instances"], plan["max_instances"]) == (8, "8Gi", 0, 1)
    assert plan["concurrency"] == 20
    env = cloudrun.environment(config())
    assert not set(cloudrun.SECRET_KEYS) & env.keys()
    assert "*" not in env["TRUSTED_HOSTS"] and env["RATE_LIMIT_SCOPE"] == "global"
    assert env["OCR_ORIENTATION_MODEL"] == "PP-LCNet_x1_0_doc_ori"
    assert env["OCR_REQUESTS_PER_MINUTE"] == "30"  # 한도는 사진 한 장을 한 번으로 센다. OCR이 빨라져서 올렸다
    # 엔진 수 x 엔진당 스레드 수 = 서버 CPU 개수 (적으면 CPU가 놀고, 많으면 서로 기다린다). 엔진마다 메모리를 1~2GB 쓴다
    assert int(env["OCR_ENGINES"]) * int(env["OCR_CPU_THREADS"]) == plan["cpu"]
    assert int(env["OCR_ENGINES"]) * 2.5 <= int(plan["memory"].removesuffix("Gi"))


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
    assert "--cpu=8" in command
    assert "--cpu-boost" in command  # 잠든 서버가 깨어날 때(모델 로딩) CPU를 더 받는다
    assert "--concurrency=20" in command  # 줄 서서 기다리는 업로드가 가벼운 요청을 막지 않게
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
        engine = PaddleOcrEngine("korean")
        engine._load(engine._workers[0])


def test_cost_estimate_does_not_assume_ten_dollars_caps_the_bill():
    assert estimate(30_000, 1000)["cloud_run_usd"] == 0
    assert estimate(300_000, 10_000)["after_pro_credit_usd"] == pytest.approx(5.18)
    assert estimate(0, 0, other_costs=12)["after_pro_credit_usd"] == 2
    assert estimate(100, 1, free_tier=False)["cloud_run_usd"] == pytest.approx(0.0068)


def prepare_seed(root, *, pinned="a" * 64, actual="a" * 64, size=5):
    (root / "recipe-package.json").write_text(json.dumps({"sha256": pinned}))
    seed = root / "recipe_data" / "image-seed"
    seed.mkdir(parents=True)
    (seed / "recipes.sqlite3").write_bytes(b"x" * 5)
    (seed / "manifest.json").write_text(json.dumps({"sha256": actual, "database_bytes": size}))


def not_in_the_bucket_yet(calls):
    """gcloud stand-in: the bucket has no recipe data, and nothing else may be run."""
    def gcloud(*args, **kwargs):
        calls.append(args[:2])
        if args[:2] == ("storage", "ls"):
            return None
        pytest.fail("Nothing may be uploaded or built without valid recipe data")
    return gcloud


def test_build_needs_the_prepared_recipe_data_and_stops_before_uploading_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    (tmp_path / "recipe-package.json").write_text(json.dumps({"sha256": "a" * 64}))
    deployment = cloudrun.Deployment(config(), tmp_path)
    calls = []
    monkeypatch.setattr(deployment, "gcloud", not_in_the_bucket_yet(calls))
    with pytest.raises(RuntimeError, match="prepare_recipes"):  # nothing prepared
        deployment.build()
    for name, kwargs in (("old-version", dict(actual="b" * 64)), ("truncated", dict(size=99))):
        folder = tmp_path / name
        folder.mkdir()
        prepare_seed(folder, **kwargs)
        monkeypatch.setattr(cloudrun, "ROOT", folder)
        with pytest.raises(RuntimeError, match="prepare_recipes"):
            deployment.build()
    assert set(calls) == {("storage", "ls")}  # 버킷에 있는지만 물어봤다


def test_build_continues_when_the_recipe_data_matches_the_pin(tmp_path, monkeypatch):
    prepare_seed(tmp_path)
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    calls = []

    def gcloud(*args, **kwargs):
        calls.append(args[:2])
        return "sha256:" + "c" * 64 if args[0] == "artifacts" else ""

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    deployment.build()
    assert calls == [("storage", "ls"), ("storage", "cp"), ("storage", "cp"), ("builds", "submit"), ("artifacts", "docker")]


def test_cloud_run_image_ships_the_recipe_database_without_opening_local_builds_to_it():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    cloudrun_stage = dockerfile[dockerfile.index("FROM production AS cloudrun\n"):].split("\nFROM ")[0]
    assert "COPY --from=cloudrun-recipes" in cloudrun_stage and "RECIPE_DATA_DIR=/opt/recipe-data" in cloudrun_stage
    ignore = (root / ".gcloudignore").read_text(encoding="utf-8").splitlines()
    assert "!/recipe-package.json" in ignore  # 해시 검사에 필요한 핀은 소스와 함께 간다
    assert not [line for line in ignore if line.startswith("!") and ("recipe_data" in line or "zip" in line or "foodiary_data" in line)]  # DB는 소스와 함께 올리지 않는다
    build = (root / "deploy/cloudbuild.cloudrun.yaml").read_text(encoding="utf-8")
    assert "!recipe_data/image-seed" in build and "DOCKER_BUILDKIT=1" in build
    # 버킷에서 DB를 받는 단계는 Docker 빌드보다 앞에 있다
    assert 0 < build.index("${_RECIPE_SEED}/*") < build.index("--target, cloudrun")
    assert "recipe_data" in (root / ".dockerignore").read_text(encoding="utf-8")  # 개발 빌드 컨텍스트에는 계속 들어가지 않는다


def stub_release_steps(deployment, monkeypatch, calls, previous="foodiary-00007-abc", failing=None):
    monkeypatch.setattr(deployment, "previous_revision", lambda: previous)
    for step in cloudrun.RELEASE_STEPS:
        def run(step=step):
            calls.append(step)
            if step == failing:
                raise RuntimeError(f"{step} failed")
        monkeypatch.setattr(deployment, step, run)


def test_release_runs_build_migrate_deploy_and_publish_in_order(tmp_path, monkeypatch, capsys):
    deployment, _ = prepare_deployment(tmp_path)
    calls = []
    stub_release_steps(deployment, monkeypatch, calls)
    deployment.release()
    assert calls == ["build", "migrate", "deploy", "publish"]
    out = capsys.readouterr().out
    assert "foodiary-00007-abc" in out and "[1/4] build" in out and "[4/4] publish" in out


@pytest.mark.parametrize("failing, ran", [("build", ["build"]), ("migrate", ["build", "migrate"]),
                                          ("deploy", ["build", "migrate", "deploy"]),
                                          ("publish", ["build", "migrate", "deploy", "publish"])])
def test_release_stops_at_the_first_failure(tmp_path, monkeypatch, capsys, failing, ran):
    deployment, _ = prepare_deployment(tmp_path)
    calls = []
    stub_release_steps(deployment, monkeypatch, calls, failing=failing)
    with pytest.raises(RuntimeError, match=f"{failing} failed"):
        deployment.release()
    assert calls == ran
    assert f"Stopped at {failing}" in capsys.readouterr().err


def test_a_failure_before_deploy_says_the_service_is_untouched(tmp_path, monkeypatch, capsys):
    deployment, _ = prepare_deployment(tmp_path)
    stub_release_steps(deployment, monkeypatch, [], failing="migrate")
    with pytest.raises(RuntimeError):
        deployment.release()
    err = capsys.readouterr().err
    assert "was not changed" in err and "update-traffic" not in err


def test_a_failure_after_deploy_shows_how_to_go_back(tmp_path, monkeypatch, capsys):
    deployment, _ = prepare_deployment(tmp_path)
    stub_release_steps(deployment, monkeypatch, [], failing="publish")
    with pytest.raises(RuntimeError):
        deployment.release()
    err = capsys.readouterr().err
    assert "may be private" in err
    assert "update-traffic foodiary --region=asia-northeast1 --to-revisions=foodiary-00007-abc=100" in err
    assert "--member=allUsers --role=roles/run.invoker" in err


def test_first_release_has_no_revision_to_go_back_to(tmp_path, monkeypatch, capsys):
    deployment, _ = prepare_deployment(tmp_path)
    stub_release_steps(deployment, monkeypatch, [], previous=None, failing="deploy")
    with pytest.raises(RuntimeError):
        deployment.release()
    assert "update-traffic" not in capsys.readouterr().err


def test_previous_revision_is_best_effort(tmp_path, monkeypatch):
    deployment, _ = prepare_deployment(tmp_path)
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: "foodiary-00009-xyz")
    assert deployment.previous_revision() == "foodiary-00009-xyz"
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: None)  # 서비스가 아직 없다
    assert deployment.previous_revision() is None
    monkeypatch.setattr(deployment, "gcloud", lambda *args, **kwargs: "")
    assert deployment.previous_revision() is None

    def broken(*args, **kwargs):
        raise RuntimeError("gcloud run services failed")
    monkeypatch.setattr(deployment, "gcloud", broken)
    assert deployment.previous_revision() is None  # 못 알아내도 배포를 막지 않는다


def test_release_is_a_cli_command(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config()))
    ran = []
    monkeypatch.setattr(cloudrun.Deployment, "release", lambda self: ran.append(self.service))
    monkeypatch.setattr(cloudrun.sys, "argv", ["cloudrun", "--config", str(config_path), "release"])
    cloudrun.main()
    assert ran == ["foodiary"]


def bucket_listing(location, names=cloudrun.RECIPE_SEED_FILES):
    return "\n".join(f"{location}/{name}" for name in names)


def test_recipe_data_already_in_the_bucket_is_not_uploaded_again_and_needs_no_local_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)  # 이 폴더에는 recipe_data가 없다
    (tmp_path / "recipe-package.json").write_text(json.dumps({"sha256": "a" * 64}))
    deployment = cloudrun.Deployment(config(), tmp_path)
    location = "gs://example-project-foodiary-builds/recipe-seed/" + "a" * 64
    assert deployment.recipe_seed_location() == location
    calls = []

    def gcloud(*args, **kwargs):
        calls.append(args)
        return bucket_listing(location) if args[:2] == ("storage", "ls") else pytest.fail("Nothing may be uploaded")

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    assert deployment.ensure_recipe_seed() == location
    assert calls == [("storage", "ls", f"{location}/*")]
    assert "not uploading it again" in capsys.readouterr().out.lower()


def test_missing_recipe_data_is_uploaded_database_first_and_manifest_last(tmp_path, monkeypatch):
    prepare_seed(tmp_path)
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    location = deployment.recipe_seed_location()
    calls = []

    def gcloud(*args, **kwargs):
        calls.append(args)
        return None if args[:2] == ("storage", "ls") else ""

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    assert deployment.ensure_recipe_seed() == location
    seed = tmp_path / "recipe_data" / "image-seed"
    assert calls[1:] == [("storage", "cp", str(seed / "recipes.sqlite3"), f"{location}/recipes.sqlite3"),
                         ("storage", "cp", str(seed / "manifest.json"), f"{location}/manifest.json")]


def test_an_interrupted_upload_is_redone(tmp_path, monkeypatch):
    prepare_seed(tmp_path)
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    location = deployment.recipe_seed_location()
    calls = []

    def gcloud(*args, **kwargs):
        calls.append(args[:2])
        return bucket_listing(location, ("recipes.sqlite3",)) if args[:2] == ("storage", "ls") else ""  # manifest.json이 없다

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    deployment.ensure_recipe_seed()
    assert calls.count(("storage", "cp")) == 2


def test_a_failed_bucket_check_falls_back_to_uploading(tmp_path, monkeypatch):
    prepare_seed(tmp_path)
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    calls = []

    def gcloud(*args, **kwargs):
        calls.append(args[:2])
        if args[:2] == ("storage", "ls"):
            raise RuntimeError("gcloud storage ls failed")
        return ""

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    deployment.ensure_recipe_seed()
    assert calls == [("storage", "ls"), ("storage", "cp"), ("storage", "cp")]


def test_a_new_recipe_version_gets_its_own_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    folders = []
    for pin in ("a" * 64, "b" * 64):
        (tmp_path / "recipe-package.json").write_text(json.dumps({"sha256": pin}))
        folders.append(deployment.recipe_seed_location())
    assert folders[0] != folders[1] and folders[1].endswith("/recipe-seed/" + "b" * 64)


@pytest.mark.parametrize("pin", [None, "", "short", "../../x", "A" * 64])
def test_the_recipe_pin_must_be_a_plain_hash(tmp_path, monkeypatch, pin):
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    (tmp_path / "recipe-package.json").write_text(json.dumps({} if pin is None else {"sha256": pin}))
    with pytest.raises(RuntimeError, match="recipe-package.json"):
        cloudrun.Deployment(config(), tmp_path).recipe_seed_location()
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path / "nowhere")
    with pytest.raises(RuntimeError, match="recipe-package.json"):
        cloudrun.Deployment(config(), tmp_path).recipe_seed_location()


def test_build_hands_the_bucket_folder_to_cloud_build_and_uploads_no_recipe_data_with_the_source(tmp_path, monkeypatch):
    prepare_seed(tmp_path)
    monkeypatch.setattr(cloudrun, "ROOT", tmp_path)
    deployment = cloudrun.Deployment(config(), tmp_path)
    location = deployment.recipe_seed_location()
    submitted = []

    def gcloud(*args, **kwargs):
        if args[:2] == ("builds", "submit"):
            submitted.append(args)
        if args[:2] == ("storage", "ls"):
            return bucket_listing(location)  # 이미 올라가 있다
        return "sha256:" + "c" * 64 if args[0] == "artifacts" else ""

    monkeypatch.setattr(deployment, "gcloud", gcloud)
    deployment.build()
    (arguments,) = submitted
    substitutions = next(a for a in arguments if a.startswith("--substitutions="))
    assert f"_RECIPE_SEED={location}" in substitutions and "_IMAGE=" in substitutions
    assert "," not in location  # 치환 값은 쉼표로 이어 붙이므로 쉼표가 들어가면 안 된다


def test_gcloud_treats_no_matching_objects_as_missing_without_noise(tmp_path, monkeypatch, capsys):
    deployment = cloudrun.Deployment(config(), tmp_path)
    monkeypatch.setattr(cloudrun.shutil, "which", lambda name: "/usr/bin/gcloud")

    def run(command, **kwargs):
        return cloudrun.subprocess.CompletedProcess(command, 1, "", "ERROR: (gcloud.storage.ls) One or more URLs matched no objects.\n")

    monkeypatch.setattr(cloudrun.subprocess, "run", run)
    assert deployment.gcloud("storage", "ls", "gs://bucket/x/*", capture=True, missing_ok=True) is None
    assert capsys.readouterr().err == ""
    with pytest.raises(RuntimeError):  # missing_ok가 아니면 여전히 실패다
        deployment.gcloud("storage", "ls", "gs://bucket/x/*", capture=True)


def write_config(tmp_path, **changes):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**config(), **changes}))
    return path


def test_add_origin_keeps_the_rest_of_the_private_config_and_the_existing_origins(tmp_path):
    path = write_config(tmp_path)
    before = json.loads(path.read_text())
    merged = cloudrun.add_origins(path, ["https://foodiary.kr/", " HTTPS://WWW.Foodiary.kr ", "https://app.example.com"])
    assert merged == ["https://app.example.com", "https://foodiary.kr", "https://www.foodiary.kr"]  # 있던 것 먼저, 중복과 끝의 /는 정리
    after = json.loads(path.read_text())
    assert after["origins"] == merged and {k: v for k, v in after.items() if k != "origins"} == {k: v for k, v in before.items() if k != "origins"}
    assert cloudrun.environment(after)["CORS_ORIGINS"] == ",".join(merged)  # 다음 deploy에 그대로 들어간다
    assert cloudrun.add_origins(path, ["https://foodiary.kr"]) == merged  # 다시 해도 같다


@pytest.mark.parametrize("bad", ["http://foodiary.kr", "https://foodiary.kr/app", "https://user:pw@foodiary.kr", "https://foodiary.kr?x=1", "foodiary.kr", "*"])
def test_add_origin_rejects_unsafe_origins_without_changing_the_file(tmp_path, bad):
    path = write_config(tmp_path)
    before = path.read_text()
    with pytest.raises(ValueError):
        cloudrun.add_origins(path, ["https://foodiary.kr", bad])
    assert path.read_text() == before  # 하나라도 틀리면 아무것도 바꾸지 않는다


def test_add_origin_is_an_offline_cli_command(tmp_path, monkeypatch, capsys):
    path = write_config(tmp_path)
    monkeypatch.setattr(cloudrun.shutil, "which", lambda name: pytest.fail("add-origin must not look for or run gcloud"))
    monkeypatch.setattr(cloudrun.sys, "argv", ["cloudrun", "--config", str(path), "add-origin", "https://foodiary.kr"])
    cloudrun.main()
    assert "https://foodiary.kr" in json.loads(path.read_text())["origins"]
    assert "foodiary.kr" in capsys.readouterr().out


def test_existing_cloudrun_service_can_proxy_external_web():
    value = config()
    value.update(web_upstream='https://web.foodiary.test', public_origin='https://foodiary.kr')
    cloudrun.validate_config(value)
    env = cloudrun.environment(value)
    assert env['WEB_UPSTREAM'] == value['web_upstream']
    assert 'foodiary.kr' in env['TRUSTED_HOSTS'].split(',')
    value['web_upstream'] = value['public_origin']
    with pytest.raises(ValueError, match='point back'):
        cloudrun.validate_config(value)
