"""친구 관계와 냉장고 권한을 판단한다.

냉장고에서 내 권한은 넷 중 하나다.
- owner: 주인
- editor: 주인처럼 고칠 수 있는 사람 (FridgeMember, role=member)
- viewer: 볼 수만 있고 변경 요청만 보낼 수 있는 사람 (FridgeViewer, 또는 공개 범위가 허락한 사람)
- None: 아무것도 못 한다
"""

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.models import ChangeRequest, Fridge, FridgeMember, FridgeViewer, Friendship
from app.timeutil import utcnow


def friendship_between(db: Session, first_id: int, second_id: int) -> Friendship | None:
    return db.scalar(
        select(Friendship).where(
            or_(
                and_(Friendship.requester_id == first_id, Friendship.addressee_id == second_id),
                and_(Friendship.requester_id == second_id, Friendship.addressee_id == first_id),
            )
        )
    )


def are_friends(db: Session, first_id: int, second_id: int) -> bool:
    friendship = friendship_between(db, first_id, second_id)
    return friendship is not None and friendship.status == "accepted"


def make_friends(db: Session, first_id: int, second_id: int) -> bool:
    """둘을 친구로 만든다. 새로 친구가 되었으면 True. (commit은 부른 쪽에서 한다)"""
    existing = friendship_between(db, first_id, second_id)
    if existing is None:
        db.add(Friendship(requester_id=first_id, addressee_id=second_id, status="accepted", responded_at=utcnow()))
        return True
    if existing.status != "accepted":  # 예전 요청 방식의 흔적
        existing.status, existing.responded_at = "accepted", utcnow()
        return True
    return False


def explicit_permission(db: Session, user_id: int, fridge_id: int) -> str | None:
    """멤버나 보기 권한으로 직접 받은 권한: owner / editor / viewer / None. 공개 범위로 보이는 것은 세지 않는다."""
    role = db.scalar(select(FridgeMember.role).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id))
    if role is not None:
        return "owner" if role == "owner" else "editor"
    if db.scalar(select(FridgeViewer.user_id).where(FridgeViewer.fridge_id == fridge_id, FridgeViewer.user_id == user_id)):
        return "viewer"
    return None


def fridge_permission(db: Session, viewer_id: int, fridge: Fridge) -> str | None:
    """이 냉장고에서 내 권한. 직접 받은 권한이 없으면 주인이 정한 공개 범위(friends 친구들도 / public 모두)가 보기 권한을 준다."""
    granted = explicit_permission(db, viewer_id, fridge.id)
    if granted is not None:
        return granted
    if fridge.visibility == "public":
        return "viewer"
    if fridge.visibility == "friends" and are_friends(db, viewer_id, fridge.owner_id):
        return "viewer"
    return None


def can_request_changes(db: Session, viewer_id: int, fridge: Fridge, permission: str | None) -> bool:
    """변경 요청은 보기 권한이 있는 친구만 보낼 수 있다. (모르는 사람이 공개 냉장고에 마구 보내지 못하게)"""
    return permission == "viewer" and are_friends(db, viewer_id, fridge.owner_id)


def _revoke(db: Session, owner_id: int, user_id: int) -> None:
    """owner의 냉장고에서 user가 받은 권한(보기, 고치기)을 모두 거두고, 보내 둔 변경 요청은 취소한다."""
    fridge_ids = select(Fridge.id).where(Fridge.owner_id == owner_id)
    db.execute(delete(FridgeViewer).where(FridgeViewer.user_id == user_id, FridgeViewer.fridge_id.in_(fridge_ids)))
    db.execute(
        delete(FridgeMember).where(
            FridgeMember.user_id == user_id, FridgeMember.role == "member", FridgeMember.fridge_id.in_(fridge_ids)
        )
    )
    db.execute(
        update(ChangeRequest)
        .where(ChangeRequest.requester_id == user_id, ChangeRequest.status == "pending", ChangeRequest.fridge_id.in_(fridge_ids))
        .values(status="cancelled", handled_at=utcnow())
    )


def end_friendship(db: Session, first_id: int, second_id: int) -> bool:
    """친구를 끊는다. 서로의 냉장고에서 받았던 권한도 함께 거둔다. 친구가 아니었으면 False. (commit은 부른 쪽에서 한다)"""
    friendship = friendship_between(db, first_id, second_id)
    if friendship is None or friendship.status != "accepted":
        return False
    db.delete(friendship)
    _revoke(db, first_id, second_id)
    _revoke(db, second_id, first_id)
    return True
