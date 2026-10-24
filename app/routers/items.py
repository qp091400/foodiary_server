"""냉장고 속 식품: 목록, 임박 목록, 수동 추가, 수정, 먹음/버림."""

import math
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.models import ConsumptionLog, FoodItem
from app.schemas import ConsumeRequest, ItemCreate, ItemOut, ItemUpdate
from app.services.expiry import estimate
from app.services.foodsafety import lookup_barcode
from app.timeutil import today, utcnow

router = APIRouter(prefix="/fridges/{fridge_id}/items", tags=["식품"])

_EPSILON = 1e-9  # 수량은 float라서 0.1 + 0.2 같은 오차를 감안한다


def _get_item(db: Session, fridge_id: int, item_id: int, *, lock: bool = False) -> FoodItem:
    item = db.get(FoodItem, item_id, with_for_update=lock)
    if item is None or item.fridge_id != fridge_id:
        raise HTTPException(status_code=404, detail="식품을 찾을 수 없습니다.")
    return item


def _round_won(value: float) -> int:
    return math.floor(value + 0.5)


@router.get("", response_model=list[ItemOut], summary="식품 목록")
def list_items(
    status: Literal["active", "consumed", "discarded", "all"] = "active",
    category: str | None = None,
    q: str | None = Query(None, description="이름 검색"),
    sort: Literal["expiry", "recent"] = Query("expiry", description="expiry=소비기한 임박순, recent=최근 추가순"),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    query = select(FoodItem).where(FoodItem.fridge_id == access.fridge.id)
    if status != "all":
        query = query.where(FoodItem.status == status)
    if category:
        query = query.where(FoodItem.category == category)
    if q and q.strip():
        query = query.where(FoodItem.name.icontains(q.strip(), autoescape=True))
    if sort == "recent":
        query = query.order_by(FoodItem.created_at.desc(), FoodItem.id.desc())
    else:  # 소비기한 없는 것(NULL)은 맨 뒤
        query = query.order_by(FoodItem.expiry_date.is_(None), FoodItem.expiry_date, FoodItem.id)
    return db.scalars(query).all()


@router.get("/expiring", response_model=list[ItemOut], summary="소비기한 임박 목록 (지난 것 포함)")
def list_expiring(
    days: int = Query(3, ge=0, le=365, description="오늘부터 며칠 안에 끝나는 것까지 볼지"),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(FoodItem)
        .where(
            FoodItem.fridge_id == access.fridge.id,
            FoodItem.status == "active",
            FoodItem.expiry_date <= today() + timedelta(days=days),
        )
        .order_by(FoodItem.expiry_date, FoodItem.id)
    ).all()


@router.post("", response_model=ItemOut, status_code=201, summary="식품 수동 추가")
def create_item(
    payload: ItemCreate,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    purchased_on = payload.purchased_on or today()
    # 바코드가 있으면 C005 소비기한을 우선 쓴다 (API 키가 없으면 조회하지 않는다).
    product = lookup_barcode(db, payload.barcode).product if payload.barcode else None
    guess = estimate(payload.name, purchased_on, product, category=payload.category)

    unit_price, total_price = payload.unit_price, payload.total_price
    if total_price is None and unit_price is not None:
        total_price = _round_won(unit_price * payload.quantity)
    elif unit_price is None and total_price is not None:
        unit_price = _round_won(total_price / payload.quantity)

    item = FoodItem(
        fridge_id=access.fridge.id,
        name=payload.name,
        category=guess.category,
        storage=payload.storage or guess.storage,
        barcode=payload.barcode,
        quantity=payload.quantity,
        remaining_quantity=payload.quantity,
        unit_price=unit_price,
        total_price=total_price,
        purchased_on=purchased_on,
        expiry_date=payload.expiry_date or guess.expiry_date,
        expiry_source="manual" if payload.expiry_date else guess.expiry_source,
        memo=payload.memo,
        added_by=access.user.id,
    )
    db.add(item)
    db.commit()
    return item


@router.get("/{item_id}", response_model=ItemOut, summary="식품 상세")
def get_item(item_id: int, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    return _get_item(db, access.fridge.id, item_id)


@router.patch("/{item_id}", response_model=ItemOut, summary="식품 수정")
def update_item(
    item_id: int,
    payload: ItemUpdate,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    item = _get_item(db, access.fridge.id, item_id, lock=True)
    changes = payload.model_dump(exclude_unset=True)

    if "quantity" in changes or "remaining_quantity" in changes:
        if item.status != "active":
            raise HTTPException(status_code=400, detail="이미 다 먹었거나 버린 식품의 수량은 수정할 수 없습니다.")
        quantity = changes.get("quantity", item.quantity)
        if "remaining_quantity" in changes:
            remaining = changes["remaining_quantity"]
        else:  # 전체 수량만 고치면 이미 먹은 양은 그대로 두고 남은 양을 같이 옮긴다
            remaining = item.remaining_quantity + (quantity - item.quantity)
        if remaining > quantity + _EPSILON:
            raise HTTPException(status_code=400, detail="남은 수량이 전체 수량보다 클 수 없습니다.")
        if remaining <= _EPSILON:
            raise HTTPException(
                status_code=400,
                detail="남은 수량이 0이 됩니다. 다 먹었거나 버렸다면 먹음/버림 처리를 이용해 주세요.",
            )
        changes["remaining_quantity"] = remaining

    if "expiry_date" in changes and changes["expiry_date"] != item.expiry_date:
        item.expiry_source = "manual"
    for name, value in changes.items():
        setattr(item, name, value)
    db.commit()
    return item


@router.delete("/{item_id}", status_code=204, summary="잘못 넣은 식품 삭제 (구매 통계에서도 빠짐)")
def delete_item(item_id: int, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    db.delete(_get_item(db, access.fridge.id, item_id, lock=True))
    db.commit()
    return Response(status_code=204)


def _finish(
    action: Literal["consume", "discard"],
    item_id: int,
    payload: ConsumeRequest | None,
    access: FridgeAccess,
    db: Session,
) -> FoodItem:
    item = _get_item(db, access.fridge.id, item_id, lock=True)
    if item.status != "active":
        raise HTTPException(status_code=400, detail="이미 다 먹었거나 버린 식품입니다.")
    quantity = payload.quantity if payload and payload.quantity is not None else item.remaining_quantity
    if quantity > item.remaining_quantity + _EPSILON:
        raise HTTPException(
            status_code=400,
            detail=f"남은 수량({item.remaining_quantity:g})보다 많이 처리할 수 없습니다.",
        )

    # 실제 단가: 할인 반영 실결제액 ÷ 구매 수량. 없으면 단가, 그것도 없으면 0원.
    if item.total_price is not None and item.quantity > 0:
        unit_value = item.total_price / item.quantity
    else:
        unit_value = item.unit_price or 0
    db.add(
        ConsumptionLog(
            fridge_id=item.fridge_id,
            food_item_id=item.id,
            item_name=item.name,
            category=item.category,
            user_id=access.user.id,
            action=action,
            quantity=quantity,
            value=_round_won(unit_value * quantity),
            logged_on=today(),
        )
    )
    item.remaining_quantity = max(0.0, item.remaining_quantity - quantity)
    if item.remaining_quantity <= _EPSILON:
        item.remaining_quantity = 0.0
        item.status = "consumed" if action == "consume" else "discarded"
        item.finished_at = utcnow()
    db.commit()
    return item


@router.post("/{item_id}/consume", response_model=ItemOut, summary="먹음 처리 (수량을 비우면 남은 양 전부)")
def consume_item(
    item_id: int,
    payload: ConsumeRequest | None = None,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    return _finish("consume", item_id, payload, access, db)


@router.post("/{item_id}/discard", response_model=ItemOut, summary="버림 처리 (수량을 비우면 남은 양 전부)")
def discard_item(
    item_id: int,
    payload: ConsumeRequest | None = None,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    return _finish("discard", item_id, payload, access, db)
