"""상품 바코드 조회, 카테고리 목록."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import BARCODE_RE, BARCODE_RULE, BarcodeLookupOut, CategoryOut
from app.services.expiry import CATEGORIES, estimate
from app.services.foodsafety import lookup_barcode
from app.timeutil import today

router = APIRouter(tags=["바코드·카테고리"])


@router.get(
    "/products/barcode/{code}",
    response_model=BarcodeLookupOut,
    summary="상품 바코드로 제품·소비기한 조회 (식품안전나라 C005)",
)
def get_product_by_barcode(code: str, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not BARCODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail=BARCODE_RULE)
    result = lookup_barcode(db, code)
    product = result.product
    if product is None:
        return BarcodeLookupOut(found=False, source=result.source)

    guess = estimate(product.name or "", today(), product)
    return BarcodeLookupOut(
        found=True,
        name=product.name,
        food_type=product.food_type,
        manufacturer=product.manufacturer,
        shelf_life_text=product.shelf_life_text,
        shelf_life_days=product.shelf_life_days,
        suggested_category=guess.category,
        suggested_expiry_date=guess.expiry_date,
        source=result.source,
    )


@router.get("/categories", response_model=list[CategoryOut], summary="카테고리 목록 (앱의 선택 메뉴용)")
def list_categories():
    return [
        CategoryOut(key=key, label=category.label, storage=category.storage, default_days=category.days)
        for key, category in CATEGORIES.items()
    ]
