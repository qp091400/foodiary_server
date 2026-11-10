"""여러 냉장고를 합친 통계. 냉장고를 하나씩, 묶음으로, 또는 마음대로 섞어 고른다. 기간과 월 경계는 Asia/Seoul 날짜 기준이다."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FRIDGE_NOT_FOUND, get_current_user
from app.models import FoodItem, Fridge, FridgeMember, User
from app.routers.stats import _period, month_stats, purchases_query, summarize_purchases
from app.schemas import (
    ExcludedFridge,
    FridgeMonthlyStats,
    FridgePurchaseSummary,
    OverviewMonthlyStats,
    OverviewPurchaseSummary,
    PublicUser,
)
from app.services.fridge_access import fridge_permission
from app.services.groups import group_fridges, my_group
from app.timeutil import today

router = APIRouter(prefix="/stats", tags=["구매 기록·통계"])

MAX_PICKS = 50  # 한 번에 고를 수 있는 냉장고·묶음 수 (요청 하나가 너무 커지지 않게)
GROUP_NOT_FOUND = "냉장고 묶음을 찾을 수 없어요."


@dataclass
class Scope:
    fridges: list[Fridge]  # 통계에 들어가는 냉장고 (내가 주인이거나 고칠 수 있는 것)
    excluded: list[Fridge]  # 골랐지만 보기만 할 수 있어서 뺀 냉장고


def resolve_scope(db: Session, me: User, fridge_ids: list[int], group_ids: list[int]) -> Scope:
    """아무것도 고르지 않으면 내가 주인이거나 고칠 수 있는 냉장고 전부. 고르면 고른 냉장고와 묶음 안 냉장고를 합친다.

    - 내가 볼 수 없는 냉장고나 묶음은 있는지도 알려 주지 않고 404.
    - 보기만 할 수 있는 냉장고는 통계에서 빼고 excluded로 알려 준다 (묶음에는 들어 있을 수 있다).
    - 묶음 안에서 내가 볼 수 없는 냉장고는 조용히 건너뛴다.
    """
    if not fridge_ids and not group_ids:
        fridges = db.scalars(select(Fridge).join(FridgeMember, FridgeMember.fridge_id == Fridge.id).where(FridgeMember.user_id == me.id)).all()
        return Scope(fridges=_ordered(me, fridges), excluded=[])

    chosen: dict[int, Fridge] = {}
    for fridge_id in dict.fromkeys(fridge_ids):
        fridge = db.get(Fridge, fridge_id)
        if fridge is None or fridge_permission(db, me.id, fridge) is None:
            raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)
        chosen[fridge.id] = fridge
    for group_id in dict.fromkeys(group_ids):
        group = my_group(db, me.id, group_id)
        if group is None:
            raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
        for fridge in group_fridges(db, group):
            if fridge_permission(db, me.id, fridge) is not None:
                chosen[fridge.id] = fridge

    counted, excluded = [], []
    for fridge in chosen.values():
        (counted if fridge_permission(db, me.id, fridge) in ("owner", "editor") else excluded).append(fridge)
    return Scope(fridges=_ordered(me, counted), excluded=_ordered(me, excluded))


def _ordered(me: User, fridges) -> list[Fridge]:
    """내 냉장고가 먼저, 그다음 번호 순."""
    return sorted(fridges, key=lambda fridge: (fridge.owner_id != me.id, fridge.id))


def _label(db: Session, fridge: Fridge) -> dict:
    return {"fridge_id": fridge.id, "fridge_name": fridge.name, "owner": PublicUser.of(db.get(User, fridge.owner_id))}


def _excluded(db: Session, scope: Scope) -> list[ExcludedFridge]:
    return [ExcludedFridge(**_label(db, fridge)) for fridge in scope.excluded]


PICK_FRIDGES = Query(default=[], max_length=MAX_PICKS, description="합쳐 볼 냉장고 번호 (여러 번 보내면 여러 개). fridge_ids·group_ids를 모두 비우면 내가 주인이거나 고칠 수 있는 냉장고 전부")
PICK_GROUPS = Query(default=[], max_length=MAX_PICKS, description="합쳐 볼 냉장고 묶음 번호 (여러 번 보내면 여러 개). fridge_ids와 섞어도 된다")


@router.get(
    "/monthly",
    response_model=OverviewMonthlyStats,
    summary="여러 냉장고를 합친 월별 지출·낭비 통계 (합계 + 냉장고별 내역)",
)
def overview_monthly(
    year: int | None = Query(None, ge=2000, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    fridge_ids: list[int] = PICK_FRIDGES,
    group_ids: list[int] = PICK_GROUPS,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = today()
    year, month = year or now.year, month or now.month
    scope = resolve_scope(db, me, fridge_ids, group_ids)
    total = month_stats(db, [fridge.id for fridge in scope.fridges], year, month)
    fridges = [FridgeMonthlyStats(**month_stats(db, [fridge.id], year, month).model_dump(), **_label(db, fridge)) for fridge in scope.fridges]
    return OverviewMonthlyStats(**total.model_dump(), fridges=fridges, excluded=_excluded(db, scope))


@router.get(
    "/purchases/summary",
    response_model=OverviewPurchaseSummary,
    summary="여러 냉장고를 합친 구매 집계 (합계 + 냉장고별 내역)",
)
def overview_purchase_summary(
    start: date | None = Query(None, description="시작일. start·end를 모두 비우면 이번 달"),
    end: date | None = None,
    group_by: Literal["item", "category"] = "item",
    fridge_ids: list[int] = PICK_FRIDGES,
    group_ids: list[int] = PICK_GROUPS,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    start, end = _period(start, end)
    scope = resolve_scope(db, me, fridge_ids, group_ids)

    def items_of(ids: list[int]) -> list[FoodItem]:
        return list(db.scalars(purchases_query(ids, start, end).order_by(FoodItem.purchased_on.desc(), FoodItem.id.desc())).all())

    groups, total_spent = summarize_purchases(items_of([fridge.id for fridge in scope.fridges]), group_by)
    fridges = []
    for fridge in scope.fridges:
        fridge_groups, fridge_total = summarize_purchases(items_of([fridge.id]), group_by)
        fridges.append(FridgePurchaseSummary(**_label(db, fridge), total_spent=fridge_total, groups=fridge_groups))
    return OverviewPurchaseSummary(
        start=start, end=end, group_by=group_by, total_spent=total_spent, groups=groups, fridges=fridges, excluded=_excluded(db, scope)
    )
