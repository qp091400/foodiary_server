"""냉장고와 공유(초대코드)."""

import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FridgeAccess, get_current_user, get_fridge_access, require_owner
from app.models import Fridge, FridgeInvite, FridgeMember, User
from app.schemas import (
    FridgeCreate,
    FridgeDetail,
    FridgeOut,
    FridgeUpdate,
    InviteOut,
    JoinRequest,
    MemberOut,
)
from app.security import generate_invite_code
from app.timeutil import utcnow

router = APIRouter(prefix="/fridges", tags=["냉장고"])

INVITE_TTL = timedelta(days=7)


def _member_count(db: Session, fridge_id: int) -> int:
    return db.scalar(select(func.count()).select_from(FridgeMember).where(FridgeMember.fridge_id == fridge_id)) or 0


def _fridge_out(db: Session, fridge: Fridge, role: str) -> FridgeOut:
    return FridgeOut(
        id=fridge.id,
        name=fridge.name,
        owner_id=fridge.owner_id,
        my_role=role,
        member_count=_member_count(db, fridge.id),
        created_at=fridge.created_at,
        visibility="private" if fridge.visibility == "friends" else fridge.visibility,
    )


@router.get("", response_model=list[FridgeOut], summary="내가 속한 냉장고 목록")
def list_fridges(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    counts = (
        select(FridgeMember.fridge_id, func.count().label("member_count"))
        .group_by(FridgeMember.fridge_id)
        .subquery()
    )
    rows = db.execute(
        select(Fridge, FridgeMember.role, counts.c.member_count)
        .join(FridgeMember, FridgeMember.fridge_id == Fridge.id)
        .join(counts, counts.c.fridge_id == Fridge.id)
        .where(FridgeMember.user_id == user.id)
        .order_by(Fridge.id)
    ).all()
    return [
        FridgeOut(
            id=fridge.id,
            name=fridge.name,
            owner_id=fridge.owner_id,
            my_role=role,
            member_count=member_count,
            created_at=fridge.created_at,
            visibility="private" if fridge.visibility == "friends" else fridge.visibility,
        )
        for fridge, role, member_count in rows
    ]


@router.post("", response_model=FridgeOut, status_code=201, summary="새 냉장고 만들기")
def create_fridge(payload: FridgeCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fridge = Fridge(name=payload.name, owner_id=user.id)
    db.add(fridge)
    db.flush()
    db.add(FridgeMember(fridge_id=fridge.id, user_id=user.id, role="owner"))
    db.commit()
    return _fridge_out(db, fridge, "owner")


@router.post("/join", response_model=FridgeOut, summary="초대코드로 냉장고 참여")
def join_fridge(payload: JoinRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.scalar(select(FridgeInvite).where(FridgeInvite.code == payload.code))
    fridge = db.get(Fridge, invite.fridge_id) if invite and invite.expires_at >= utcnow() else None
    if fridge is None:
        raise HTTPException(status_code=404, detail="초대코드가 올바르지 않거나 만료되었습니다.")

    already = "이미 참여 중인 냉장고입니다."
    if db.scalar(
        select(FridgeMember.id).where(FridgeMember.fridge_id == fridge.id, FridgeMember.user_id == user.id)
    ):
        raise HTTPException(status_code=409, detail=already)
    db.add(FridgeMember(fridge_id=fridge.id, user_id=user.id, role="member"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=already) from None
    return _fridge_out(db, fridge, "member")


@router.get("/{fridge_id}", response_model=FridgeDetail, summary="냉장고 상세 + 멤버 목록")
def get_fridge(access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    rows = db.execute(
        select(FridgeMember, User)
        .join(User, User.id == FridgeMember.user_id)
        .where(FridgeMember.fridge_id == access.fridge.id)
        .order_by(FridgeMember.joined_at, FridgeMember.id)
    ).all()
    members = [
        MemberOut(
            user_id=user.id,
            username=user.username,
            nickname=user.nickname,
            role=member.role,
            joined_at=member.joined_at,
        )
        for member, user in rows
    ]
    return FridgeDetail(
        id=access.fridge.id,
        name=access.fridge.name,
        owner_id=access.fridge.owner_id,
        my_role=access.member.role,
        member_count=len(members),
        created_at=access.fridge.created_at,
        visibility="private" if access.fridge.visibility == "friends" else access.fridge.visibility,
        members=members,
    )


@router.patch("/{fridge_id}", response_model=FridgeOut, summary="냉장고 이름·공개 범위 변경 (주인만)")
def update_fridge(
    payload: FridgeUpdate,
    access: FridgeAccess = Depends(require_owner),
    db: Session = Depends(get_db),
):
    if payload.name is not None:
        access.fridge.name = payload.name
    if payload.visibility is not None:
        access.fridge.visibility = payload.visibility
    db.commit()
    return _fridge_out(db, access.fridge, access.member.role)


@router.delete("/{fridge_id}", status_code=204, summary="냉장고 삭제 (주인만, 식품·영수증 함께 삭제)")
def delete_fridge(access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    fridge_id = access.fridge.id
    db.delete(access.fridge)  # 멤버·초대·식품·영수증·기록은 FK ON DELETE CASCADE로 지워진다
    db.commit()
    shutil.rmtree(Path(settings.upload_dir) / str(fridge_id), ignore_errors=True)
    return Response(status_code=204)


@router.post("/{fridge_id}/invites", response_model=InviteOut, status_code=201, summary="초대코드 만들기 (7일 유효)")
def create_invite(access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    for _ in range(5):  # 코드가 겹치면 다시 뽑는다
        invite = FridgeInvite(
            fridge_id=access.fridge.id,
            code=generate_invite_code(),
            created_by=access.user.id,
            expires_at=utcnow() + INVITE_TTL,
        )
        db.add(invite)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return InviteOut(code=invite.code, expires_at=invite.expires_at)
    raise HTTPException(status_code=503, detail="초대코드를 만들지 못했습니다. 다시 시도해 주세요.")


@router.delete("/{fridge_id}/members/{user_id}", status_code=204, summary="멤버 내보내기 (주인) / 나가기 (본인)")
def remove_member(
    user_id: int,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    if user_id == access.user.id:
        if access.is_owner:
            raise HTTPException(
                status_code=400,
                detail="냉장고 주인은 나갈 수 없습니다. 냉장고가 필요 없다면 삭제해 주세요.",
            )
        target = access.member
    else:
        if not access.is_owner:
            raise HTTPException(status_code=403, detail="냉장고 주인만 다른 멤버를 내보낼 수 있습니다.")
        target = db.scalar(
            select(FridgeMember).where(FridgeMember.fridge_id == access.fridge.id, FridgeMember.user_id == user_id)
        )
        if target is None:
            raise HTTPException(status_code=404, detail="이 냉장고의 멤버가 아닙니다.")
    db.delete(target)
    db.commit()
    return Response(status_code=204)
