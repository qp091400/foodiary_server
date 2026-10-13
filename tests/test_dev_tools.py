from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Receipt
from app.services.ocr import get_ocr_engine
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


def post(client, name="receipt.jpg", data=None):
    return client.post(URL, files={"file": (name, data if data is not None else image_bytes(), "image/jpeg")})


@pytest.fixture
def dev_tools(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_tools", True)


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
