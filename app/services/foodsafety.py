"""식품안전나라 오픈API C005 "바코드연계제품정보" 조회 + ProductCache.

필드명·결과 코드는 공식 C005 문서와 공개 샘플 응답으로 확인했다.
바코드 연계정보는 2018년 이후 최신화가 중단되어 최신 상품 조회를 보장하지 않는다.
키가 없거나 조회가 실패하면 카테고리 추정으로 넘어간다.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import ProductCache
from app.services.expiry import parse_shelf_life_days
from app.timeutil import utcnow

logger = logging.getLogger(__name__)
# httpx는 INFO 레벨로 요청 URL을 찍는데, C005는 URL 경로에 API 키가 들어간다.
logging.getLogger("httpx").setLevel(logging.WARNING)

BASE_URL = "http://openapi.foodsafetykorea.go.kr/api"
SERVICE = "C005"
CACHE_TTL = timedelta(days=30)
TIMEOUT_SECONDS = 5.0
_NO_DATA_CODE = "INFO-200"
_OK_CODE = "INFO-000"


class FoodSafetyError(Exception):
    """네트워크 오류나 해석할 수 없는 응답. 캐시하지 않는다."""


@dataclass(frozen=True)
class ProductInfo:
    barcode: str
    name: str | None = None
    food_type: str | None = None
    manufacturer: str | None = None
    shelf_life_text: str | None = None
    shelf_life_days: int | None = None


@dataclass(frozen=True)
class BarcodeLookup:
    source: str  # cache / api / disabled / error
    product: ProductInfo | None = None

    @property
    def found(self) -> bool:
        return self.product is not None


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    return str(value).strip() or None


def parse_c005(payload: Any, barcode: str) -> ProductInfo | None:
    """C005 응답 JSON → ProductInfo. 제품이 없으면 None, 해석할 수 없으면 FoodSafetyError."""
    if not isinstance(payload, dict):
        raise FoodSafetyError("응답이 JSON 객체가 아님")
    body = payload.get(SERVICE)
    if not isinstance(body, dict):
        body = payload  # 오류 응답은 서비스 이름 없이 RESULT만 오기도 한다
    result = body.get("RESULT")
    code = _text(result, "CODE") if isinstance(result, dict) else None

    rows = body.get("row")
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    if rows:
        row = next((r for r in rows if _text(r, "BAR_CD") == barcode), rows[0])
        shelf_life_text = _text(row, "POG_DAYCNT")
        return ProductInfo(
            barcode=barcode,
            name=_text(row, "PRDLST_NM"),
            food_type=_text(row, "PRDLST_DCNM"),
            manufacturer=_text(row, "BSSH_NM"),
            shelf_life_text=shelf_life_text,
            shelf_life_days=parse_shelf_life_days(shelf_life_text),
        )
    if code == _NO_DATA_CODE or (code in (None, _OK_CODE) and str(body.get("total_count", "")).strip() == "0"):
        return None
    message = _text(result, "MSG") if isinstance(result, dict) else None
    raise FoodSafetyError(f"C005 오류 응답 (code={code}, msg={message})")


def fetch_c005(barcode: str) -> ProductInfo | None:
    """C005를 실제로 호출한다. 실패하면 FoodSafetyError."""
    url = f"{BASE_URL}/{settings.foodsafety_api_key}/{SERVICE}/json/1/5/BAR_CD={barcode}"
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        # 예외 메시지에 URL(=API 키)이 들어 있을 수 있어 종류만 남긴다.
        raise FoodSafetyError(f"요청 실패: {type(exc).__name__}") from None
    except ValueError:
        raise FoodSafetyError("JSON이 아닌 응답 (API 키가 잘못됐을 때 이렇게 온다)") from None
    return parse_c005(payload, barcode)


def _from_cache(row: ProductCache) -> ProductInfo | None:
    if not row.found:
        return None
    return ProductInfo(
        barcode=row.barcode,
        name=row.name,
        food_type=row.food_type,
        manufacturer=row.manufacturer,
        shelf_life_text=row.shelf_life_text,
        shelf_life_days=row.shelf_life_days,
    )


def _clip(value: str | None, length: int) -> str | None:
    return value[:length] if value else None


def _save_cache(barcode: str, product: ProductInfo | None) -> None:
    """요청 트랜잭션과 분리된 세션에 저장한다. 캐시 저장 실패가 본 작업을 깨뜨리면 안 된다."""
    found = product or ProductInfo(barcode)
    try:
        with SessionLocal() as session:
            session.merge(
                ProductCache(
                    barcode=barcode,
                    found=product is not None,
                    name=_clip(found.name, 200),
                    food_type=_clip(found.food_type, 100),
                    manufacturer=_clip(found.manufacturer, 200),
                    shelf_life_text=_clip(found.shelf_life_text, 300),
                    shelf_life_days=found.shelf_life_days,
                    fetched_at=utcnow(),
                )
            )
            session.commit()
    except Exception as exc:
        logger.warning("product_cache 저장 실패 (barcode=%s): %s", barcode, exc)


def lookup_barcode(db: Session, barcode: str) -> BarcodeLookup:
    """상품 바코드로 제품 정보를 찾는다.

    - API 키가 없으면 source="disabled"
    - 30일 안에 조회한 적이 있으면 source="cache" ("없음"도 캐시된다)
    - 네트워크·응답 오류는 source="error"이고 캐시하지 않는다
    """
    if not settings.foodsafety_api_key.strip():
        return BarcodeLookup("disabled")

    cached = db.get(ProductCache, barcode)
    if cached is not None and cached.fetched_at >= utcnow() - CACHE_TTL:
        return BarcodeLookup("cache", _from_cache(cached))

    try:
        product = fetch_c005(barcode)
    except FoodSafetyError as exc:
        logger.warning("C005 조회 실패 (barcode=%s): %s", barcode, exc)
        return BarcodeLookup("error")
    _save_cache(barcode, product)
    if cached is not None:
        db.expire(cached)  # 다른 세션에서 갱신했으니 다음 조회 때 다시 읽게 한다
    return BarcodeLookup("api", product)
