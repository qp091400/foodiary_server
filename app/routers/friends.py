"""친구: 코드·닉네임으로 요청하고, 받은 요청을 수락하거나 거절한다."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Friendship, User
from app.schemas import FriendOut, FriendRequestOut, FriendRequestResult, FriendTarget, PublicUser
from app.services.friends import friendship_between
from app.timeutil import utcnow

router = APIRouter(prefix="/friends", tags=["친구"])

# 보내 놓고 답을 못 받은 친구 요청은 이만큼까지만 쌓인다. 아무에게나 마구 보내는 것을 막는다.
MAX_PENDING_REQUESTS = 50  # TODO: balance
NOT_FOUND = "그런 사용자를 찾을 수 없어요."


def _resolve_target(db: Session, me: User, target: FriendTarget) -> User:
    if target.code is not None:
        found = db.scalar(select(User).where(User.friend_code == target.code))
    elif target.user_id is not None:
        found = db.get(User, target.user_id)
    else:
        matches = db.scalars(
            select(User).where(func.lower(User.nickname) == target.nickname.lower(), User.id != me.id).limit(2)
        ).all()
        if len(matches) > 1:
            raise HTTPException(
                status_code=409, detail="같은 닉네임을 쓰는 사람이 여러 명이에요. 검색해서 고르거나 친구 코드를 써 주세요."
            )
        found = matches[0] if matches else None
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    if found.id == me.id:
        raise HTTPException(status_code=400, detail="나에게는 친구 요청을 보낼 수 없어요.")
    return found


@router.post("/requests", response_model=FriendRequestResult, status_code=201, summary="친구 요청 보내기 (코드, 닉네임, 사용자 번호 중 하나)")
def send_request(target: FriendTarget, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    other = _resolve_target(db, me, target)
    existing = friendship_between(db, me.id, other.id)
    if existing is not None:
        if existing.status == "accepted":
            raise HTTPException(status_code=409, detail="이미 친구예요.")
        if existing.requester_id == me.id:
            raise HTTPException(status_code=409, detail="이미 친구 요청을 보냈어요.")
        # 상대가 먼저 요청해 둔 상태 → 서로 원하는 것이니 바로 친구가 된다
        existing.status, existing.responded_at = "accepted", utcnow()
        db.commit()
        return FriendRequestResult(status="friends", user=PublicUser(id=other.id, nickname=other.nickname))

    pending = db.scalar(
        select(func.count()).select_from(Friendship).where(Friendship.requester_id == me.id, Friendship.status == "pending")
    )
    if pending >= MAX_PENDING_REQUESTS:
        raise HTTPException(
            status_code=429, detail="답을 기다리는 친구 요청이 너무 많아요. 상대가 수락하거나 내가 취소한 뒤에 다시 보내 주세요."
        )
    request = Friendship(requester_id=me.id, addressee_id=other.id)
    db.add(request)
    try:
        db.commit()
    except IntegrityError:  # 같은 사람에게 동시에 두 번 눌렀다
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 친구 요청을 보냈어요.") from None
    return FriendRequestResult(
        status="requested", user=PublicUser(id=other.id, nickname=other.nickname), request_id=request.id
    )


@router.get("/requests", response_model=list[FriendRequestOut], summary="친구 요청 목록 (받은 것 incoming / 보낸 것 outgoing)")
def list_requests(
    direction: Literal["incoming", "outgoing"] = Query("incoming"),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mine, theirs = (
        (Friendship.addressee_id, Friendship.requester_id) if direction == "incoming" else (Friendship.requester_id, Friendship.addressee_id)
    )
    rows = db.execute(
        select(Friendship, User)
        .join(User, User.id == theirs)
        .where(mine == me.id, Friendship.status == "pending")
        .order_by(Friendship.created_at.desc(), Friendship.id.desc())
    ).all()
    return [
        FriendRequestOut(id=row.id, user=PublicUser(id=user.id, nickname=user.nickname), created_at=row.created_at)
        for row, user in rows
    ]


def _pending_for(db: Session, request_id: int, **who: int) -> Friendship:
    """내가 받은(addressee_id=) 또는 보낸(requester_id=) 아직 답이 없는 요청. 아니면 404."""
    row = db.get(Friendship, request_id)
    if row is None or row.status != "pending" or any(getattr(row, field) != value for field, value in who.items()):
        raise HTTPException(status_code=404, detail="친구 요청을 찾을 수 없어요.")
    return row


@router.post("/requests/{request_id}/accept", response_model=FriendOut, summary="받은 친구 요청 수락")
def accept_request(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _pending_for(db, request_id, addressee_id=me.id)
    row.status, row.responded_at = "accepted", utcnow()
    db.commit()
    sender = db.get(User, row.requester_id)
    return FriendOut(user_id=sender.id, nickname=sender.nickname, since=row.responded_at)


@router.post("/requests/{request_id}/decline", status_code=204, summary="받은 친구 요청 거절")
def decline_request(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_pending_for(db, request_id, addressee_id=me.id))
    db.commit()
    return Response(status_code=204)


@router.delete("/requests/{request_id}", status_code=204, summary="내가 보낸 친구 요청 취소")
def cancel_request(request_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_pending_for(db, request_id, requester_id=me.id))
    db.commit()
    return Response(status_code=204)


@router.get("", response_model=list[FriendOut], summary="내 친구 목록")
def list_friends(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Friendship).where(
            Friendship.status == "accepted", (Friendship.requester_id == me.id) | (Friendship.addressee_id == me.id)
        )
    ).all()
    friend_of = {row.addressee_id if row.requester_id == me.id else row.requester_id: row for row in rows}
    users = db.scalars(select(User).where(User.id.in_(friend_of))).all() if friend_of else []
    friends = [FriendOut(user_id=user.id, nickname=user.nickname, since=friend_of[user.id].responded_at) for user in users]
    return sorted(friends, key=lambda friend: (friend.nickname.lower(), friend.user_id))


@router.delete("/{user_id}", status_code=204, summary="친구 끊기")
def remove_friend(user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = friendship_between(db, me.id, user_id)
    if row is None or row.status != "accepted":
        raise HTTPException(status_code=404, detail="친구가 아니에요.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)
