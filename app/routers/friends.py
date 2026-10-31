"""친구 목록, 친구 코드로 추가, 끊기. 친구는 친구 코드나 냉장고 공유 링크(routers/sharing.py)로 맺는다."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware import request_limiter
from app.models import Friendship, User
from app.schemas import FriendAdd, FriendOut, PublicUser
from app.services.friends import end_friendship, friendship_between, make_friends

router = APIRouter(prefix="/friends", tags=["친구"])

CODE_TRIES_PER_MINUTE = 10  # TODO: balance. 한 사람이 1분에 넣어 볼 수 있는 친구 코드 수 (틀린 코드도 센다. 코드를 아무거나 넣어 맞히는 것을 막는다)


@router.get("", response_model=list[FriendOut], summary="내 친구 목록")
def list_friends(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Friendship).where(
            Friendship.status == "accepted", (Friendship.requester_id == me.id) | (Friendship.addressee_id == me.id)
        )
    ).all()
    since = {row.addressee_id if row.requester_id == me.id else row.requester_id: row.responded_at or row.created_at for row in rows}
    users = db.scalars(select(User).where(User.id.in_(since))).all() if since else []
    friends = [FriendOut(user=PublicUser.of(user), since=since[user.id]) for user in users]
    return sorted(friends, key=lambda friend: (friend.user.nickname.lower(), friend.user.id))


@router.post("", response_model=FriendOut, status_code=201, summary="친구 코드로 친구 추가: 바로 서로 친구가 된다 (이미 친구면 200)")
def add_friend(payload: FriendAdd, response: Response, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if settings.rate_limit_enabled:
        retry_after = request_limiter.check((f"user:{me.id}", "friend_code"), CODE_TRIES_PER_MINUTE)
        if retry_after:
            raise HTTPException(status_code=429, detail="친구 코드를 너무 많이 넣었어요. 잠시 후에 다시 해 주세요.", headers={"Retry-After": str(retry_after)})
    friend = db.scalar(select(User).where(User.friend_code == payload.friend_code))
    if friend is None:
        raise HTTPException(status_code=404, detail="친구 코드를 찾을 수 없어요. 코드를 다시 확인해 주세요.")
    if friend.id == me.id:
        raise HTTPException(status_code=400, detail="내 친구 코드예요.")
    created = make_friends(db, me.id, friend.id)
    if created:
        try:
            db.commit()
        except IntegrityError:  # 같은 사람에게 동시에 두 번 보냈다. 이미 친구가 된 것과 같다
            db.rollback()
            created = False
    if not created:
        response.status_code = 200
    friendship = friendship_between(db, me.id, friend.id)
    return FriendOut(user=PublicUser.of(friend), since=friendship.responded_at or friendship.created_at)


@router.delete("/{user_id}", status_code=204, summary="친구 끊기 (서로의 냉장고에서 받았던 권한도 함께 사라진다)")
def remove_friend(user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not end_friendship(db, me.id, user_id):
        raise HTTPException(status_code=404, detail="친구가 아니에요.")
    db.commit()
    return Response(status_code=204)
