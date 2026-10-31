"""변경 요청: 보기 권한이 있는 친구가 냉장고 주인에게 "먹었어요", "두고 갔어요"를 보내고, 주인이 승인하면 냉장고에 반영된다."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FridgeAccess, get_current_user
from app.models import ChangeRequest, Fridge, FoodItem, FridgeMember, User
from app.routers.items import consume_item, create_item, discard_item
from app.schemas import ChangeRequestCreate, ChangeRequestOut, ConsumeRequest, ItemCreate, PublicUser
from app.services.fridge_access import can_request_changes, fridge_permission
from app.timeutil import utcnow

router = APIRouter(prefix="/change-requests", tags=["변경 요청"])

MAX_PENDING_PER_FRIDGE = 20  # TODO: balance. 한 사람이 한 냉장고에 답을 못 받은 요청을 쌓을 수 있는 수
NOT_FOUND = "변경 요청을 찾을 수 없어요."
Status = Literal["pending", "approved", "declined", "cancelled", "all"]


def _out(db: Session, request: ChangeRequest) -> ChangeRequestOut:
    fridge = db.get(Fridge, request.fridge_id)
    requester = db.get(User, request.requester_id)
    payload = request.payload or {}
    return ChangeRequestOut(
        id=request.id,
        fridge_id=request.fridge_id,
        fridge_name=fridge.name,
        requester=PublicUser.of(requester),
        kind=request.kind,
        item_id=request.item_id,
        item_name=request.item_name,
        quantity=payload.get("quantity"),
        category=payload.get("category"),
        storage=payload.get("storage"),
        expiry_date=payload.get("expiry_date"),
        note=request.note,
        status=request.status,
        created_at=request.created_at,
        handled_at=request.handled_at,
    )


# ── 보내는 사람 ──────────────────────────────────────────────────────────


@router.post("", response_model=ChangeRequestOut, status_code=201, summary="구버전 변경 요청 (신규 발행 종료)")
def send_request(payload: ChangeRequestCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fridge = db.get(Fridge, payload.fridge_id)
    permission = fridge_permission(db, me.id, fridge) if fridge else None
    if permission is None:
        raise HTTPException(status_code=404, detail="냉장고를 찾을 수 없어요.")  # 볼 수 없는 냉장고는 있는지도 알려 주지 않는다
    if permission in ("owner", "editor"):
        raise HTTPException(status_code=400, detail="고칠 수 있는 냉장고라서 요청하지 않고 직접 바꾸면 돼요.")
    if not can_request_changes(db, me.id, fridge, permission):
        raise HTTPException(status_code=403, detail="보기 전용 참여자는 수정할 수 없어요. 냉장고 주인에게 함께 관리 권한을 요청해주세요.")
    pending = db.scalar(
        select(func.count()).select_from(ChangeRequest).where(
            ChangeRequest.fridge_id == fridge.id, ChangeRequest.requester_id == me.id, ChangeRequest.status == "pending"
        )
    )
    if pending >= MAX_PENDING_PER_FRIDGE:
        raise HTTPException(status_code=429, detail="답을 기다리는 요청이 너무 많아요. 주인이 처리한 뒤에 다시 보내 주세요.")

    item_name = payload.name
    if payload.kind != "add":
        item = db.get(FoodItem, payload.item_id)
        if item is None or item.fridge_id != fridge.id or item.status != "active":
            raise HTTPException(status_code=404, detail="식품을 찾을 수 없어요.")
        if payload.quantity is not None and payload.quantity > item.remaining_quantity + 1e-9:
            raise HTTPException(status_code=400, detail=f"남은 수량({item.remaining_quantity:g})보다 많이 요청할 수 없어요.")
        item_name = item.name
    request = ChangeRequest(
        fridge_id=fridge.id,
        requester_id=me.id,
        kind=payload.kind,
        item_id=payload.item_id,
        item_name=item_name,
        payload={
            "quantity": payload.quantity,
            "category": payload.category,
            "storage": payload.storage,
            "expiry_date": payload.expiry_date.isoformat() if payload.expiry_date else None,
        },
        note=payload.note,
    )
    db.add(request)
    db.commit()
    return _out(db, request)


@router.get("/sent", response_model=list[ChangeRequestOut], summary="내가 보낸 변경 요청 (최신순)")
def list_sent(status: Status = Query("all"), limit: int = Query(50, ge=1, le=200), me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(ChangeRequest).where(ChangeRequest.requester_id == me.id).order_by(ChangeRequest.id.desc()).limit(limit)
    if status != "all":
        query = query.where(ChangeRequest.status == status)
    return [_out(db, request) for request in db.scalars(query).all()]


@router.delete("/{request_id}", status_code=204, summary="내가 보낸 요청 취소 (아직 처리 전일 때)")
def cancel_request(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    request = db.get(ChangeRequest, request_id)
    if request is None or request.requester_id != me.id or request.status != "pending":
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    request.status, request.handled_at = "cancelled", utcnow()
    db.commit()
    return Response(status_code=204)


# ── 냉장고 주인 ──────────────────────────────────────────────────────────


@router.get("/received", response_model=list[ChangeRequestOut], summary="내 냉장고들에 들어온 변경 요청 (기본은 처리 전인 것, 오래된 것부터)")
def list_received(
    status: Status = Query("pending"),
    fridge_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(ChangeRequest)
        .join(Fridge, Fridge.id == ChangeRequest.fridge_id)
        .where(Fridge.owner_id == me.id)
        .order_by(ChangeRequest.id)
        .limit(limit)
    )
    if status != "all":
        query = query.where(ChangeRequest.status == status)
    if fridge_id is not None:
        query = query.where(ChangeRequest.fridge_id == fridge_id)
    return [_out(db, request) for request in db.scalars(query).all()]


def _mine_to_handle(db: Session, request_id: int, me: User) -> tuple[ChangeRequest, Fridge]:
    request = db.get(ChangeRequest, request_id)
    fridge = db.get(Fridge, request.fridge_id) if request else None
    if request is None or fridge is None or fridge.owner_id != me.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="이미 처리한 요청이에요.")
    return request, fridge


@router.post("/{request_id}/approve", response_model=ChangeRequestOut, summary="요청 승인: 그 내용이 냉장고에 반영된다 (주인만)")
def approve(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    request, fridge = _mine_to_handle(db, request_id, me)
    owner_member = db.scalar(select(FridgeMember).where(FridgeMember.fridge_id == fridge.id, FridgeMember.user_id == me.id))
    requester = db.get(User, request.requester_id)
    # 반영한 기록에는 요청한 사람이 남는다(누가 먹었는지, 누가 넣었는지). 승인은 주인이 한다.
    access = FridgeAccess(fridge=fridge, member=owner_member, user=requester)
    payload = request.payload or {}
    # 승인 표시를 먼저 해 두면, 아래에서 냉장고에 반영하며 하는 commit 한 번에 같이 저장된다 (반영만 되고 요청은 처리 전으로 남는 일이 없다)
    request.status, request.handled_by, request.handled_at = "approved", me.id, utcnow()
    try:
        if request.kind == "add":
            create_item(
                ItemCreate(
                    name=request.item_name,
                    quantity=payload.get("quantity") or 1,
                    category=payload.get("category"),
                    storage=payload.get("storage"),
                    expiry_date=payload.get("expiry_date"),
                    memo=request.note,
                ),
                access,
                db,
            )
        else:
            action = consume_item if request.kind == "consume" else discard_item
            action(request.item_id, ConsumeRequest(quantity=payload.get("quantity")), access, db)
    except HTTPException:
        # 품목이 그 사이 없어졌거나 이미 다 먹었다. 요청은 처리 전으로 남기고 주인이 거절하면 된다
        db.rollback()
        raise
    return _out(db, request)


@router.post("/{request_id}/decline", response_model=ChangeRequestOut, summary="요청 거절 (주인만)")
def decline(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    request, _ = _mine_to_handle(db, request_id, me)
    request.status, request.handled_by, request.handled_at = "declined", me.id, utcnow()
    db.commit()
    return _out(db, request)
