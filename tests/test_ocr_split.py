"""API server and OCR server split: the API sends each photo to the OCR server and builds the draft from the boxes."""

import pytest
from fastapi.testclient import TestClient

from app import ocr_server
from app.main import app
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError
from app.services.ocr.remote import RemoteOcrEngine
from tests.helpers import FakeOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup

ROWS = [
    ["이마트 성수점"],
    ["2026-10-10 14:22"],
    ["상품명", "단가", "수량", "금액"],
    ["서울우우 1L", "2,980", "1", "2,980"],
    ["풀무원 두부", "1,500", "2", "3,000"],
    ["합계", "5,980"],
]


class BusyEngine:
    def recognize(self, image_path):
        raise OcrBusyError()


@pytest.fixture
def ocr(monkeypatch):
    """The OCR server with a fake OCR engine."""
    state = {"engine": FakeOcrEngine(make_boxes(ROWS))}
    monkeypatch.setattr(ocr_server, "get_ocr_engine", lambda: state["engine"])
    state["client"] = TestClient(ocr_server.app)
    return state


@pytest.fixture
def remote(ocr):
    engine = RemoteOcrEngine("http://ocr", engines=2, client=ocr["client"])
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    yield engine
    app.dependency_overrides.pop(get_ocr_engine, None)


def upload(client):
    tokens = signup(client, "minsu")
    fridge = default_fridge_id(client, tokens)
    return client.post(f"/fridges/{fridge}/receipts", headers=auth(tokens), files={"file": ("r.jpg", image_bytes(), "image/jpeg")})


def test_the_ocr_server_returns_the_boxes(ocr):
    response = ocr["client"].post("/read", files={"file": ("r.jpg", image_bytes(), "image/jpeg")}, data={"wait": "5", "ticket": "7"})
    assert response.status_code == 200, response.text
    assert sorted(box["text"] for box in response.json()["boxes"]) == sorted(box.text for box in make_boxes(ROWS))
    assert ocr["client"].get("/ready").json() == {"ocr": "ready"}


def test_the_ocr_server_says_busy_with_503(ocr):
    ocr["engine"] = BusyEngine()
    response = ocr["client"].post("/read", files={"file": ("r.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 503 and response.headers["Retry-After"] == "10"


def test_a_receipt_read_by_the_ocr_server_becomes_the_same_draft(client, remote):
    response = upload(client)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert [(item["name"], item["amount"]) for item in draft["draft_items"]] == [("서울우유 1L", 2980), ("풀무원 두부", 3000)]
    assert draft["store_name"] == "이마트 성수점" and draft["total_amount"] == 5980


def test_a_busy_ocr_server_gives_503_to_the_app(client, remote, ocr):
    ocr["engine"] = BusyEngine()
    response = upload(client)
    assert response.status_code == 503 and response.headers["Retry-After"] == "10"


def test_the_api_calls_the_ocr_server_with_a_google_id_token(ocr):
    seen = []

    class Recorder:
        def get(self, url, **kwargs):
            seen.append((url, kwargs.get("headers")))
            if url.endswith("/ready"):
                return ocr["client"].get("/ready")
            return type("Token", (), {"text": "id-token\n", "raise_for_status": lambda self: None})()

    engine = RemoteOcrEngine("https://ocr.example", auth="google", client=Recorder())
    assert engine.ready()
    token_calls = [call for call in seen if "metadata" in call[0]]
    assert len(token_calls) == 1 and token_calls[0][1] == {"Metadata-Flavor": "Google"}
    assert seen[-1][1] == {"Authorization": "Bearer id-token"}
    assert engine._headers() == {"Authorization": "Bearer id-token"}
    assert len([call for call in seen if "metadata" in call[0]]) == 1  # token reused


def test_the_public_ready_check_does_not_hammer_the_ocr_service(ocr, monkeypatch):
    calls = []

    class Counter:
        def get(self, url, **kwargs):
            calls.append(url)
            return ocr["client"].get("/ready")

    engine = RemoteOcrEngine("http://ocr", client=Counter())
    assert all(engine.ready() for _ in range(20))
    assert len(calls) == 1  # a good answer is reused for READY_SECONDS
    engine._ready_until = 0
    engine._probe_lock.acquire()  # another request is probing a cold OCR service right now
    assert engine.ready() is True and len(calls) == 1  # answered from the last result, no second probe
    engine._probe_lock.release()
