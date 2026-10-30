"""친구 관계와 남의 냉장고를 볼 수 있는지 판단한다."""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Fridge, FridgeMember, Friendship


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


def relations(db: Session, me_id: int, other_ids: list[int]) -> dict[int, str]:
    """other_ids 각자와 나의 관계: friend(친구) / requested(내가 요청함) / incoming(상대가 요청함). 없으면 none."""
    if not other_ids:
        return {}
    rows = db.scalars(
        select(Friendship).where(
            or_(
                and_(Friendship.requester_id == me_id, Friendship.addressee_id.in_(other_ids)),
                and_(Friendship.addressee_id == me_id, Friendship.requester_id.in_(other_ids)),
            )
        )
    ).all()
    result = dict.fromkeys(other_ids, "none")
    for row in rows:
        other = row.addressee_id if row.requester_id == me_id else row.requester_id
        if row.status == "accepted":
            result[other] = "friend"
        else:
            result[other] = "requested" if row.requester_id == me_id else "incoming"
    return result


def can_view_fridge(db: Session, viewer_id: int, fridge: Fridge) -> bool:
    """이 냉장고의 품목을 볼 수 있는가. 멤버는 늘 볼 수 있고, 아닌 사람은 주인이 정한 공개 범위를 따른다."""
    if db.scalar(select(FridgeMember.id).where(FridgeMember.fridge_id == fridge.id, FridgeMember.user_id == viewer_id)):
        return True
    if fridge.visibility == "public":
        return True
    return fridge.visibility == "friends" and are_friends(db, viewer_id, fridge.owner_id)
