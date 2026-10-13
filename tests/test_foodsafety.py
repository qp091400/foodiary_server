from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.config import settings
from app.database import SessionLocal
from app.models import ProductCache
from app.services import foodsafety
from app.services.foodsafety import FoodSafetyError, lookup_barcode, parse_c005
from app.timeutil import today, utcnow
from tests.helpers import auth, default_fridge_id, signup

BARCODE = "8801234567890"

# C005 응답 형식 (요청서에 적힌 필드명 기준. 공식 문서로 검증하지 못한 가정이다)
FOUND = {
    "C005": {
        "total_count": "1",
        "row": [
            {
                "PRDLST_NM": "맛있는 초코 과자",
                "POG_DAYCNT": "제조일로부터 12개월",
                "PRDLST_DCNM": "과자",
                "BSSH_NM": "(주)테스트제과",
                "BAR_CD": BARCODE,
            }
        ],
        "RESULT": {"MSG": "정상처리되었습니다.", "CODE": "INFO-000"},
    }
}
NOT_FOUND = {"C005": {"total_count": "0", "RESULT": {"MSG": "해당하는 데이터가 없습니다.", "CODE": "INFO-200"}}}
BAD_KEY = {"RESULT": {"MSG": "인증키가 유효하지 않습니다.", "CODE": "INFO-100"}}


class FakeC005:
    """httpx.get 대역: 호출을 세고 준비된 응답을 돌려준다."""

    def __init__(self, monkeypatch, payload=None, *, error: Exception | None = None, text: str | None = None):
        self.urls: list[str] = []
        self.payload, self.error, self.text = payload, error, text
        monkeypatch.setattr(foodsafety.httpx, "get", self)

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        if self.error:
            raise self.error
        request = httpx.Request("GET", url)
        if self.text is not None:
            return httpx.Response(200, text=self.text, request=request)
        return httpx.Response(200, json=self.payload, request=request)


@pytest.fixture
def api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "foodsafety_api_key", "test-key")


def test_parse_c005():
    product = parse_c005(FOUND, BARCODE)
    assert product.name == "맛있는 초코 과자"
    assert product.food_type == "과자"
    assert product.manufacturer == "(주)테스트제과"
    assert (product.shelf_life_text, product.shelf_life_days) == ("제조일로부터 12개월", 360)

    assert parse_c005(NOT_FOUND, BARCODE) is None
    # 필드가 빠져 있어도 죽지 않고 None으로 채운다
    sparse = parse_c005({"C005": {"row": [{"BAR_CD": BARCODE, "POG_DAYCNT": "별도표기"}]}}, BARCODE)
    assert (sparse.name, sparse.food_type, sparse.shelf_life_days) == (None, None, None)
    # 여러 건이면 바코드가 일치하는 행을 고른다
    many = {"C005": {"row": [{"BAR_CD": "111", "PRDLST_NM": "다른 것"}, {"BAR_CD": BARCODE, "PRDLST_NM": "이것"}]}}
    assert parse_c005(many, BARCODE).name == "이것"

    for broken in (BAD_KEY, {}, [], "oops", {"C005": {"row": "?"}}):
        with pytest.raises(FoodSafetyError):
            parse_c005(broken, BARCODE)


def test_lookup_calls_api_then_hits_cache(client, api_key, monkeypatch):
    api = FakeC005(monkeypatch, FOUND)
    with SessionLocal() as db:
        first = lookup_barcode(db, BARCODE)
    assert (first.source, first.found) == ("api", True)
    assert first.product.shelf_life_days == 360
    assert api.urls == [f"http://openapi.foodsafetykorea.go.kr/api/test-key/C005/json/1/5/BAR_CD={BARCODE}"]

    with SessionLocal() as db:
        second = lookup_barcode(db, BARCODE)
        assert (second.source, second.product) == ("cache", first.product)
        cached = db.get(ProductCache, BARCODE)
        assert (cached.found, cached.name, cached.shelf_life_days) == (True, "맛있는 초코 과자", 360)
    assert len(api.urls) == 1  # 두 번째는 API를 부르지 않았다


def test_not_found_is_cached_but_errors_are_not(client, api_key, monkeypatch):
    api = FakeC005(monkeypatch, NOT_FOUND)
    with SessionLocal() as db:
        assert (lookup_barcode(db, BARCODE).source, lookup_barcode(db, BARCODE).source) == ("api", "cache")
        assert lookup_barcode(db, BARCODE).found is False
    assert len(api.urls) == 1

    other = "8809999999999"
    failures = (
        {"error": httpx.ConnectTimeout("timeout")},
        {"payload": BAD_KEY},
        {"text": "<script>alert('인증키가 유효하지 않습니다.')</script>"},
    )
    for failure in failures:
        failing = FakeC005(monkeypatch, **failure)
        with SessionLocal() as db:
            result = lookup_barcode(db, other)
            assert (result.source, result.found) == ("error", False)
            assert db.get(ProductCache, other) is None  # 오류는 캐시하지 않는다
        assert len(failing.urls) == 1


def test_cache_expires_after_30_days(client, api_key, monkeypatch):
    api = FakeC005(monkeypatch, FOUND)
    with SessionLocal() as db:
        lookup_barcode(db, BARCODE)
        db.execute(update(ProductCache).values(fetched_at=utcnow() - timedelta(days=31)))
        db.commit()
    with SessionLocal() as db:
        assert lookup_barcode(db, BARCODE).source == "api"
        assert lookup_barcode(db, BARCODE).source == "cache"
        assert db.scalar(select(ProductCache.fetched_at)) > utcnow() - timedelta(minutes=1)
    assert len(api.urls) == 2


def test_disabled_without_api_key(client, monkeypatch):
    api = FakeC005(monkeypatch, FOUND)
    tokens = signup(client, "minsu")
    with SessionLocal() as db:
        assert lookup_barcode(db, BARCODE).source == "disabled"

    response = client.get(f"/products/barcode/{BARCODE}", headers=auth(tokens))
    assert response.status_code == 200
    assert response.json()["source"] == "disabled"
    assert response.json()["found"] is False
    assert api.urls == []


def test_barcode_endpoint(client, api_key, monkeypatch):
    api = FakeC005(monkeypatch, FOUND)
    tokens = signup(client, "minsu")
    headers = auth(tokens)

    response = client.get(f"/products/barcode/{BARCODE}", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "found": True,
        "name": "맛있는 초코 과자",
        "food_type": "과자",
        "manufacturer": "(주)테스트제과",
        "shelf_life_text": "제조일로부터 12개월",
        "shelf_life_days": 360,
        "suggested_category": "snack",
        "suggested_expiry_date": (today() + timedelta(days=360)).isoformat(),
        "source": "api",
    }
    assert client.get(f"/products/barcode/{BARCODE}", headers=headers).json()["source"] == "cache"
    assert len(api.urls) == 1

    for invalid in ("1234567", "123456789012345", "88012345abcde"):
        bad = client.get(f"/products/barcode/{invalid}", headers=headers)
        assert bad.status_code == 422, invalid
        assert "8~14자리" in bad.json()["detail"]
    assert client.get(f"/products/barcode/{BARCODE}").status_code == 401

    FakeC005(monkeypatch, error=httpx.ConnectError("down"))
    failed = client.get("/products/barcode/8800000000001", headers=headers).json()
    assert (failed["found"], failed["source"]) == (False, "error")


def test_manual_item_with_barcode_uses_c005_shelf_life(client, api_key, monkeypatch):
    FakeC005(monkeypatch, FOUND)
    tokens = signup(client, "minsu")
    fridge_id = default_fridge_id(client, tokens)
    response = client.post(
        f"/fridges/{fridge_id}/items", headers=auth(tokens), json={"name": "초코 과자", "barcode": BARCODE}
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert (item["category"], item["expiry_source"], item["days_left"]) == ("snack", "barcode", 360)
    assert item["barcode"] == BARCODE


def test_categories_endpoint(client):
    response = client.get("/categories")
    assert response.status_code == 200
    categories = {row["key"]: row for row in response.json()}
    assert categories["milk"] == {"key": "milk", "label": "우유", "storage": "fridge", "default_days": 10}
    assert categories["non_food"] == {"key": "non_food", "label": "비식품", "storage": None, "default_days": None}
    assert categories["other"]["default_days"] == 7
    assert len(categories) == 34
