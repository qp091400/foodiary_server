"""구매 기록과 월별 지출·낭비 통계. 기간과 월 경계는 Asia/Seoul 날짜 기준이다."""

import re
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.models import ConsumptionLog, FoodItem, Receipt, ReceiptItem
from app.schemas import (
    CategoryAmount,
    DiscardedCategory,
    MonthlyStats,
    PurchaseGroup,
    PurchaseOut,
    PurchaseSummary,
)
from app.services.expiry import label_of
from app.timeutil import month_bounds, today

router = APIRouter(prefix="/fridges/{fridge_id}", tags=["구매 기록·통계"])


def _period(start: date | None, end: date | None) -> tuple[date | None, date | None]:
    """둘 다 비우면 이번 달. 하나만 주면 나머지 쪽은 제한 없음."""
    if start is None and end is None:
        now = today()
        return month_bounds(now.year, now.month)
    if start and end and start > end:
        raise HTTPException(status_code=400, detail="start가 end보다 늦을 수 없습니다.")
    return start, end


def _purchases_query(fridge_id: int, start: date | None, end: date | None):
    query = select(FoodItem).where(FoodItem.fridge_id == fridge_id)
    if start:
        query = query.where(FoodItem.purchased_on >= start)
    if end:
        query = query.where(FoodItem.purchased_on <= end)
    return query


@router.get("/purchases", response_model=list[PurchaseOut], summary="산 식품 목록 (구매일 내림차순)")
def list_purchases(
    start: date | None = Query(None, description="시작일. start·end를 모두 비우면 이번 달"),
    end: date | None = None,
    q: str | None = Query(None, description="이름 검색"),
    category: str | None = None,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    start, end = _period(start, end)
    query = (
        _purchases_query(access.fridge.id, start, end)
        .add_columns(Receipt.store_name)
        .outerjoin(Receipt, Receipt.id == FoodItem.receipt_id)
    )
    if q and q.strip():
        query = query.where(FoodItem.name.icontains(q.strip(), autoescape=True))
    if category:
        query = query.where(FoodItem.category == category)
    rows = db.execute(query.order_by(FoodItem.purchased_on.desc(), FoodItem.id.desc())).all()
    return [
        PurchaseOut(
            id=item.id,
            receipt_id=item.receipt_id,
            store_name=store_name,
            name=item.name,
            category=item.category,
            category_label=label_of(item.category),
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            total_price=item.total_price,
            purchased_on=item.purchased_on,
            status=item.status,
        )
        for item, store_name in rows
    ]


@router.get("/purchases/summary", response_model=PurchaseSummary, summary="구매 집계 (예: 우유 3번, 4개, 8,940원)")
def purchase_summary(
    start: date | None = Query(None, description="시작일. start·end를 모두 비우면 이번 달"),
    end: date | None = None,
    group_by: Literal["item", "category"] = "item",
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    start, end = _period(start, end)
    items = db.scalars(
        _purchases_query(access.fridge.id, start, end).order_by(FoodItem.purchased_on.desc(), FoodItem.id.desc())
    ).all()

    groups: dict[str, PurchaseGroup] = {}
    for item in items:
        if group_by == "category":
            key, name = item.category, label_of(item.category)
        else:  # "서울우유 1L"와 "서울 우유 1l"를 같은 것으로 묶는다
            key, name = re.sub(r"\s+", "", item.name).lower(), item.name
        group = groups.setdefault(key, PurchaseGroup(key=key, name=name, count=0, quantity=0, total_spent=0))
        group.count += 1
        group.quantity += item.quantity
        group.total_spent += item.total_price or 0
    ordered = sorted(groups.values(), key=lambda group: (-group.total_spent, -group.count, group.name))
    return PurchaseSummary(
        start=start,
        end=end,
        group_by=group_by,
        total_spent=sum(group.total_spent for group in ordered),
        groups=ordered,
    )


@router.get("/stats/monthly", response_model=MonthlyStats, summary="월별 지출·낭비 통계")
def monthly_stats(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    now = today()
    year, month = year or now.year, month or now.month
    start, end = month_bounds(year, month)
    fridge_id = access.fridge.id

    # 식품 지출: 그 달에 산 식품의 실결제액
    spent_rows = db.execute(
        select(FoodItem.category, func.count(), func.coalesce(func.sum(FoodItem.total_price), 0))
        .where(FoodItem.fridge_id == fridge_id, FoodItem.purchased_on.between(start, end))
        .group_by(FoodItem.category)
    ).all()
    spent_by_category = sorted(
        (
            CategoryAmount(category=category, category_label=label_of(category), amount=int(amount), count=count)
            for category, count, amount in spent_rows
        ),
        key=lambda row: (-row.amount, row.category),
    )

    # 비식품 지출: 확정된 영수증에서 냉장고에 넣지 않은 줄
    confirmed_in_month = (
        Receipt.fridge_id == fridge_id,
        Receipt.status == "confirmed",
        Receipt.purchased_on.between(start, end),
    )
    non_food_spent = db.scalar(
        select(func.coalesce(func.sum(ReceiptItem.amount - ReceiptItem.discount), 0))
        .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
        .where(*confirmed_in_month, ReceiptItem.included.is_(False))
    )
    receipt_count = db.scalar(select(func.count()).select_from(Receipt).where(*confirmed_in_month))

    # 먹은 금액·버린 금액: 처리한 날(logged_on) 기준
    log_rows = db.execute(
        select(
            ConsumptionLog.action,
            ConsumptionLog.category,
            func.coalesce(func.sum(ConsumptionLog.value), 0),
            func.coalesce(func.sum(ConsumptionLog.quantity), 0),
            func.count(),
        )
        .where(ConsumptionLog.fridge_id == fridge_id, ConsumptionLog.logged_on.between(start, end))
        .group_by(ConsumptionLog.action, ConsumptionLog.category)
    ).all()
    consumed_value = sum(int(value) for action, _, value, _, _ in log_rows if action == "consume")
    discarded = sorted(
        (
            DiscardedCategory(
                category=category,
                category_label=label_of(category),
                value=int(value),
                quantity=float(quantity),
                count=count,
            )
            for action, category, value, quantity, count in log_rows
            if action == "discard"
        ),
        key=lambda row: (-row.value, -row.quantity, row.category),
    )
    discarded_value = sum(row.value for row in discarded)
    finished_value = consumed_value + discarded_value

    return MonthlyStats(
        year=year,
        month=month,
        food_spent=sum(row.amount for row in spent_by_category),
        non_food_spent=int(non_food_spent or 0),
        receipt_count=receipt_count or 0,
        item_count=sum(row.count for row in spent_by_category),
        spent_by_category=spent_by_category,
        consumed_value=consumed_value,
        discarded_value=discarded_value,
        waste_rate=round(discarded_value / finished_value, 4) if finished_value else 0.0,
        top_discarded=discarded[:5],
    )
