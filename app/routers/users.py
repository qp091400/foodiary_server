"""내 정보."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FRIDGE_NOT_FOUND, get_current_user
from app.models import Fridge, FoodItem, User
from app.schemas import (
    FriendCodeOut,
    PasswordChangeRequest,
    PasswordChangeResponse,
    PublicUser,
    SharedFridgeOut,
    SharedItemOut,
    UserOut,
    UserSearchOut,
    UserUpdate,
)
from app.security import (
    create_access_token,
    create_refresh_token,
    generate_invite_code,
    hash_password,
    verify_password,
)
from app.services.friends import can_view_fridge, relations

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


# ── 사람 찾기 · 친구 코드 ────────────────────────────────────────────────

NO_SUCH_USER = "그런 사용자를 찾을 수 없어요."


@router.get("/search", response_model=list[UserSearchOut], summary="닉네임으로 사람 찾기 (앞부분이 같은 사람, 최대 20명)")
def search_users(
    nickname: str = Query(min_length=2, max_length=20),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prefix = nickname.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    found = db.scalars(
        select(User)
        .where(func.lower(User.nickname).like(prefix + "%", escape="\\"), User.id != me.id)
        .order_by(User.nickname, User.id)
        .limit(20)
    ).all()
    related = relations(db, me.id, [user.id for user in found])
    return [UserSearchOut(id=user.id, nickname=user.nickname, relation=related[user.id]) for user in found]


@router.get("/lookup", response_model=UserSearchOut, summary="친구 코드로 사람 확인 (링크를 눌렀을 때 누구인지 보여 주려고)")
def lookup_user(code: str = Query(min_length=1, max_length=16), me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.friend_code == code.strip().upper()))
    if user is None:
        raise HTTPException(status_code=404, detail=NO_SUCH_USER)
    relation = "none" if user.id == me.id else relations(db, me.id, [user.id])[user.id]
    return UserSearchOut(id=user.id, nickname=user.nickname, relation=relation)


@router.post("/me/friend-code", response_model=FriendCodeOut, summary="내 친구 코드를 새로 만들기 (예전 코드와 링크는 못 쓰게 된다)")
def reset_friend_code(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for _ in range(5):  # 코드가 겹치면 다시 뽑는다
        user.friend_code = generate_invite_code()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return FriendCodeOut(friend_code=user.friend_code)
    raise HTTPException(status_code=503, detail="친구 코드를 만들지 못했어요. 다시 시도해 주세요.")


# ── 남의 냉장고 보기 (읽기 전용) ─────────────────────────────────────────


def _visible_fridges(db: Session, me: User, owner_id: int) -> list[Fridge]:
    owner = db.get(User, owner_id)
    if owner is None:
        raise HTTPException(status_code=404, detail=NO_SUCH_USER)
    fridges = db.scalars(select(Fridge).where(Fridge.owner_id == owner_id).order_by(Fridge.id)).all()
    return [fridge for fridge in fridges if can_view_fridge(db, me.id, fridge)]


@router.get("/{user_id}/fridges", response_model=list[SharedFridgeOut], summary="이 사람의 냉장고 중 내가 볼 수 있는 것")
def list_user_fridges(user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fridges = _visible_fridges(db, me, user_id)
    counts = dict(
        db.execute(
            select(FoodItem.fridge_id, func.count())
            .where(FoodItem.fridge_id.in_([fridge.id for fridge in fridges]), FoodItem.status == "active")
            .group_by(FoodItem.fridge_id)
        ).all()
    ) if fridges else {}
    return [
        SharedFridgeOut(id=fridge.id, name=fridge.name, visibility=fridge.visibility, item_count=counts.get(fridge.id, 0))
        for fridge in fridges
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
    if fridge_id not in {fridge.id for fridge in _visible_fridges(db, me, user_id)}:
        raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)  # 있는지 없는지도 알려 주지 않는다
    return db.scalars(
        select(FoodItem)
        .where(FoodItem.fridge_id == fridge_id, FoodItem.status == "active")
        .order_by(FoodItem.expiry_date.is_(None), FoodItem.expiry_date, FoodItem.name, FoodItem.id)
        .limit(limit)
    ).all()

