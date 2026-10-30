"""친구 목록과 끊기. 친구는 냉장고 공유 링크로 맺는다(routers/sharing.py)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Friendship, User
from app.schemas import FriendOut, PublicUser
from app.services.friends import end_friendship

router = APIRouter(prefix="/friends", tags=["친구"])


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


@router.delete("/{user_id}", status_code=204, summary="친구 끊기 (서로의 냉장고에서 받았던 권한도 함께 사라진다)")
def remove_friend(user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not end_friendship(db, me.id, user_id):
        raise HTTPException(status_code=404, detail="친구가 아니에요.")
    db.commit()
    return Response(status_code=204)
