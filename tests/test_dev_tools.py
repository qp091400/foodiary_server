from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Receipt
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError
from tests.helpers import FakeOcrEngine, image_bytes, make_boxes

URL = "/dev/receipt-test"
ROWS = [
    ["이마트 성수점"],
    ["2026-10-10 14:22"],
    ["상품명", "단가", "수량", "금액"],
    ["서울우유 1L", "2,980", "1", "2,980"],
    ["행사할인", "-500"],
    ["물티슈 100매", "1,000", "1", "1,000"],
    ["합계", "3,480"],
]


TEST_URL = "/receipt-test"
PASSWORD = "test-page-pass-1234"


def post(client, name="receipt.jpg", data=None, url=URL, password=None):
    headers = {"X-Test-Password": password} if password is not None else {}
    files = {"file": (name, data if data is not None else image_bytes(), "image/jpeg")}
    return client.post(url, files=files, headers=headers)


@pytest.fixture
def dev_tools(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_tools", True)


@pytest.fixture
def test_password(client, monkeypatch):
    monkeypatch.setattr(settings, "receipt_test_password", PASSWORD)


def test_disabled_by_default(client):
    # 꺼져 있으면 없는 경로와 똑같이 보인다
    assert client.get(URL).status_code == 404
    assert post(client).status_code == 404
    assert client.get(URL).json() == client.get("/no-such-path").json()
    assert not any(path.startswith("/dev") for path in client.get("/openapi.json").json()["paths"])


def test_page_is_served(client, dev_tools):
    response = client.get(URL)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "영수증 인식 테스트" in response.text


def test_recognizes_without_login_and_saves_nothing(client, dev_tools):
    engine = FakeOcrEngine(make_boxes(ROWS))
    app.dependency_overrides[get_ocr_engine] = lambda: engine

    response = post(client)  # Authorization 헤더 없음
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["store_name"] == "이마트 성수점"
    assert result["purchased_on"] == "2026-10-10"
    assert (result["total_amount"], result["items_sum"], result["warnings"]) == (3480, 3480, [])
    assert result["ocr_error"] is None and result["ocr_seconds"] >= 0
    assert "서울우유 1L 2,980 1 2,980" in result["raw_lines"]
    milk, wipes = result["draft_items"]
    assert (milk["name"], milk["discount"], milk["category"], milk["expiry_date"]) == ("서울우유 1L", 500, "milk", "2026-10-20")
    assert (wipes["category"], wipes["include"]) == ("non_food", False)

    # 영수증도 이미지도 남지 않는다
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    assert not [path for path in Path(settings.upload_dir).rglob("*") if path.is_file()]
    assert not Path(engine.calls[0]).exists()


def test_shows_why_ocr_failed(client, dev_tools):
    class BrokenEngine:
        def recognize(self, image_path):
            raise RuntimeError("model crashed")

    app.dependency_overrides[get_ocr_engine] = lambda: BrokenEngine()
    result = post(client).json()
    assert result["ocr_error"] == "RuntimeError: model crashed"
    assert result["draft_items"] == []
    assert result["warnings"] == ["품목을 찾지 못했어요. 직접 추가해 주세요"]


def test_rejects_bad_files(client, dev_tools):
    assert post(client, name="receipt.pdf").status_code == 400
    bad = post(client, data=b"not an image")
    assert bad.status_code == 400
    assert "이미지" in bad.json()["detail"]


def test_busy_ocr_is_503_like_the_real_upload(client, dev_tools):
    class BusyEngine:
        def recognize(self, image_path):
            raise OcrBusyError("busy")

    app.dependency_overrides[get_ocr_engine] = lambda: BusyEngine()
    response = post(client)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "10"
    assert response.json()["detail"] == "다른 영수증을 분석 중입니다. 잠시 후 다시 시도해 주세요."


# ── 운영용 /receipt-test: 테스트 비밀번호 ──────────────────────────────────


def test_password_page_is_off_without_a_password(client):
    assert client.get(TEST_URL).status_code == 404
    assert post(client, url=TEST_URL, password="").status_code == 404
    assert client.get(TEST_URL).json() == client.get("/no-such-path").json()


def test_password_page_requires_the_password(client, test_password):
    page = client.get(TEST_URL)
    assert page.status_code == 200
    assert "테스트 비밀번호" in page.text
    assert client.get(URL).status_code == 404  # 개발용 경로는 계속 닫혀 있다

    engine = FakeOcrEngine(make_boxes(ROWS))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    for password in (None, "", "wrong-password-1234", PASSWORD + "x"):
        response = post(client, url=TEST_URL, password=password)
        assert response.status_code == 401
        assert response.json()["detail"] == "테스트 비밀번호가 맞지 않습니다."
    assert engine.calls == []  # 비밀번호가 틀리면 OCR을 돌리지 않는다

    response = post(client, url=TEST_URL, password=PASSWORD)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    result = response.json()
    assert (result["store_name"], result["total_amount"], result["items_sum"]) == ("이마트 성수점", 3480, 3480)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0
    assert not Path(engine.calls[0]).exists()


def test_password_page_hides_ocr_error_details(client, test_password):
    class BrokenEngine:
        def recognize(self, image_path):
            raise RuntimeError("model crashed at /secret/path")

    app.dependency_overrides[get_ocr_engine] = lambda: BrokenEngine()
    response = post(client, url=TEST_URL, password=PASSWORD)
    assert response.json()["ocr_error"] == "영수증 인식에 실패했습니다."
    assert "secret" not in response.text


def test_wrong_passwords_count_toward_the_ocr_limit(client, test_password, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "ocr_requests_per_minute", 2)
    assert [post(client, url=TEST_URL, password="guess").status_code for _ in range(3)] == [401, 401, 429]
