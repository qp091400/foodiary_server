"""내 정보."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FRIDGE_NOT_FOUND, get_current_user
from app.models import FoodItem, Fridge, User
from app.schemas import (
    PasswordChangeRequest,
    PasswordChangeResponse,
    SharedFridgeOut,
    SharedItemOut,
    UserOut,
    UserUpdate,
)
from app.security import create_access_token, create_refresh_token, hash_password, verify_password
from app.services.friends import can_request_changes, fridge_permission

router = APIRouter(prefix="/users", tags=["사용자"])


@router.get("/me", response_model=UserOut, summary="내 정보")
def get_me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut, summary="닉네임 변경")
def update_me(payload: UserUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.nickname = payload.nickname
    db.commit()
    return user


@router.post("/me/password", response_model=PasswordChangeResponse, summary="비밀번호 변경")
def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="새 비밀번호가 현재 비밀번호와 같습니다.")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1  # 다른 기기의 토큰은 여기서 모두 무효가 된다
    db.commit()
    # 지금 쓰던 토큰도 무효가 됐으니, 이 기기는 새 토큰으로 로그인 상태를 이어 간다.
    return PasswordChangeResponse(
        message="비밀번호를 변경했습니다.",
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )


# ── 남의 냉장고 보기 ─────────────────────────────────────────────────────

NO_SUCH_USER = "그런 사용자를 찾을 수 없어요."


def _visible_fridges(db: Session, me: User, owner_id: int) -> list[tuple[Fridge, str]]:
    """그 사람이 주인인 냉장고 중 내가 볼 수 있는 것과 거기서 내 권한(owner / editor / viewer)."""
    if db.get(User, owner_id) is None:
        raise HTTPException(status_code=404, detail=NO_SUCH_USER)
    fridges = db.scalars(select(Fridge).where(Fridge.owner_id == owner_id).order_by(Fridge.id)).all()
    seen = [(fridge, fridge_permission(db, me.id, fridge)) for fridge in fridges]
    return [(fridge, permission) for fridge, permission in seen if permission is not None]


@router.get("/{user_id}/fridges", response_model=list[SharedFridgeOut], summary="이 사람의 냉장고 중 내가 볼 수 있는 것과 내 권한")
def list_user_fridges(user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    visible = _visible_fridges(db, me, user_id)
    counts = (
        dict(
            db.execute(
                select(FoodItem.fridge_id, func.count())
                .where(FoodItem.fridge_id.in_([fridge.id for fridge, _ in visible]), FoodItem.status == "active")
                .group_by(FoodItem.fridge_id)
            ).all()
        )
        if visible
        else {}
    )
    return [
        SharedFridgeOut(
            id=fridge.id,
            name=fridge.name,
            visibility=fridge.visibility,
            item_count=counts.get(fridge.id, 0),
            my_permission=permission,
            can_request_changes=can_request_changes(db, me.id, fridge, permission),
        )
        for fridge, permission in visible
    ]


@router.get(
    "/{user_id}/fridges/{fridge_id}/items",
    response_model=list[SharedItemOut],
    summary="이 사람의 냉장고 품목 (남아 있는 것만. 가격·메모·영수증은 안 보인다)",
)
def list_user_fridge_items(
    user_id: int,
    fridge_id: int,
    limit: int = Query(200, ge=1, le=500),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if fridge_id not in {fridge.id for fridge, _ in _visible_fridges(db, me, user_id)}:
        raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)  # 있는지 없는지도 알려 주지 않는다
    return db.scalars(
        select(FoodItem)
        .where(FoodItem.fridge_id == fridge_id, FoodItem.status == "active")
        .order_by(FoodItem.expiry_date.is_(None), FoodItem.expiry_date, FoodItem.name, FoodItem.id)
        .limit(limit)
    ).all()
