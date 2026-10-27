"""운영 시 조용한 OCR 실패, 기본 비밀키, 무제한 요청을 허용하지 않는다."""

from types import SimpleNamespace
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app
from app.middleware import RequestLimiter
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError
from app.services.ocr.paddle import PaddleOcrEngine
from tests.helpers import auth, default_fridge_id, image_bytes, signup


def production_settings(**overrides):
    values = dict(
        app_env="production",
        jwt_secret="test-production-secret-" * 3,
        database_url="postgresql+psycopg://fridge:" + "a" * 36 + "@db/fridge",
        cors_origins="https://app.example.com",
        trusted_hosts="api.example.com,localhost,127.0.0.1",
        auto_create_tables=False,
        dev_tools=False,
        ocr_engine="paddle",
        ocr_warmup=True,
        ocr_fail_closed=True,
        rate_limit_enabled=True,
        email_backend="smtp",
        smtp_user="mail@example.com",
        smtp_password="test-smtp-password",
        mail_from="mail@example.com",
    )
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize(
    "override",
    [
        {"jwt_secret": "change-me"},
        {"dev_tools": True},
        {"receipt_test_password": "short-pass"},
        {"receipt_test_password": "with spaces inside"},
        {"receipt_test_password": "한글비밀번호한글비밀번호"},
        {"auto_create_tables": True},
        {"cors_origins": "*"},
        {"trusted_hosts": "*"},
        {"ocr_engine": "none"},
        {"ocr_warmup": False},
        {"ocr_fail_closed": False},
        {"rate_limit_enabled": False},
        {"database_url": "postgresql+psycopg://fridge:fridge@db/fridge"},
        {"database_url": "not-a-database-url"},
        {"email_backend": "console"},
        {"smtp_password": ""},
    ],
)
def test_production_rejects_unsafe_configuration(override):
    with pytest.raises(ValueError, match="Unsafe production configuration"):
        production_settings(**override).validate_for_startup()


def test_production_accepts_complete_configuration():
    production_settings().validate_for_startup()
    production_settings(receipt_test_password="k7xq-2mfa-9rtz").validate_for_startup()


def test_startup_stops_if_actual_ocr_check_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ocr_warmup", True)
    def fail():
        raise RuntimeError("inference failed")
    monkeypatch.setattr("app.services.ocr.paddle.get_paddle_engine", lambda: SimpleNamespace(warmup=fail))
    with pytest.raises(RuntimeError, match="inference failed"):
        with TestClient(app):
            pass


def test_readiness_refuses_unhealthy_ocr(client, monkeypatch):
    monkeypatch.setattr(settings, "ocr_warmup", True)
    monkeypatch.setattr("app.services.ocr.paddle.get_paddle_engine", lambda: SimpleNamespace(ready=False))
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    monkeypatch.setattr("app.services.ocr.paddle.get_paddle_engine", lambda: SimpleNamespace(ready=True))
    assert client.get("/ready").status_code == 200


def test_auth_request_limit_and_security_headers(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_requests_per_minute", 1)
    body = {"username": "not-a-user", "password": "incorrect"}
    assert client.post("/auth/login", json=body).status_code == 401
    limited = client.post("/auth/login", json=body)
    assert limited.status_code == 429 and 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.headers["X-Content-Type-Options"] == "nosniff"
    assert limited.headers["Cache-Control"] == "no-store"
    assert client.get("/health").status_code == 200


def test_limit_expires_and_keys_are_bounded():
    limiter = RequestLimiter(max_keys=2)
    assert limiter.check(("one", "ocr"), 1, now=10) == 0
    assert limiter.check(("one", "ocr"), 1, now=11) == 59
    assert limiter.check(("one", "ocr"), 1, now=70) == 0
    limiter.check(("two", "ocr"), 1, now=71)
    limiter.check(("three", "ocr"), 1, now=72)
    assert len(limiter._entries) == 2


@pytest.mark.parametrize("busy", [False, True])
def test_production_ocr_failure_is_503_and_leaves_no_receipt_or_file(client, monkeypatch, busy):
    tokens = signup(client)
    fridge_id = default_fridge_id(client, tokens)
    monkeypatch.setattr(settings, "ocr_fail_closed", True)
    class BrokenEngine:
        def recognize(self, path):
            raise OcrBusyError("busy") if busy else RuntimeError("private model path")
    app.dependency_overrides[get_ocr_engine] = BrokenEngine
    response = client.post(
        f"/fridges/{fridge_id}/receipts",
        headers=auth(tokens),
        files={"file": ("receipt.jpg", image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 503
    assert "private model path" not in response.text
    assert client.get(f"/fridges/{fridge_id}/receipts", headers=auth(tokens)).json() == []
    from pathlib import Path
    assert not [path for path in Path(settings.upload_dir).rglob("*") if path.is_file()]


def test_engine_rejects_parallel_inference():
    pytest.importorskip("numpy")
    engine = PaddleOcrEngine("korean")
    engine._predict_lock.acquire()
    try:
        with pytest.raises(OcrBusyError):
            engine.recognize("not-opened.jpg")
    finally:
        engine._predict_lock.release()


@pytest.mark.parametrize("quantity", ["1e309", '"NaN"', '"Infinity"'])
def test_non_finite_quantities_are_rejected_before_saving(client, quantity):
    tokens = signup(client)
    fridge_id = default_fridge_id(client, tokens)
    response = client.post(
        f"/fridges/{fridge_id}/items",
        headers={**auth(tokens), "Content-Type": "application/json"},
        content='{"name":"sample","quantity":' + quantity + '}',
    )
    assert response.status_code == 422
    assert client.get(f"/fridges/{fridge_id}/items", headers=auth(tokens)).json() == []


def test_changed_model_files_are_rejected(tmp_path):
    from app.services.ocr.manifest import verify_model_files

    expected = {}
    for model in (settings.ocr_detection_model, settings.ocr_recognition_model, settings.ocr_textline_model):
        path = tmp_path / model / "inference.pdiparams"
        path.parent.mkdir()
        path.write_bytes(b"verified model")
        expected[f"{model}/inference.pdiparams"] = hashlib.sha256(b"verified model").hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sha256": expected}), encoding="utf-8")
    assert verify_model_files(cache=tmp_path, manifest=manifest) == expected
    path.write_bytes(b"different model")
    with pytest.raises(RuntimeError, match="checksum differs"):
        verify_model_files(cache=tmp_path, manifest=manifest)
